#!/usr/bin/env python3
"""Prepare and run the pinned native MLX-VLM runtime on Apple Silicon.

Model download is deliberately separate from start: ``prepare`` downloads the
two exact revisions, while ``start`` resolves cache entries with
``local_files_only=True`` and never starts an implicit multi-GB download.
"""
from __future__ import annotations

import argparse
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "macos_mlx_runtime.yaml"


def _config() -> dict[str, Any]:
    document = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("version") != 1:
        raise RuntimeError(f"invalid Metal runtime config: {CONFIG_PATH}")
    runtime = document.get("runtime")
    profiles = document.get("profiles")
    default_profile = document.get("default_profile")
    if not isinstance(runtime, dict) or not isinstance(profiles, dict) or default_profile not in profiles:
        raise RuntimeError(f"incomplete Metal runtime config: {CONFIG_PATH}")
    packages = runtime.get("packages")
    if (
        not isinstance(packages, list)
        or not packages
        or any(not isinstance(package, str) or package.count("==") != 1 for package in packages)
    ):
        raise RuntimeError("Metal runtime packages must be a non-empty list of exact pins")
    profile = profiles[default_profile]
    if not isinstance(profile, dict) or not isinstance(profile.get("assistant"), dict):
        raise RuntimeError("Metal default profile and assistant must be mappings")
    normal = profile.get("qualification", {}).get("normal", {})
    if int(normal.get("input_tokens", 0)) + int(normal.get("generation_tokens", 0)) != int(runtime.get("max_kv_size", 0)):
        raise RuntimeError("Metal input and generation budgets must equal runtime.max_kv_size")
    if runtime.get("turboquant", {}).get("enabled") is not False:
        raise RuntimeError("initial Metal profile must keep TurboQuant disabled")
    speculative = runtime.get("speculative_decoding", {})
    if speculative.get("enabled") is not True or speculative.get("kind") != "mtp":
        raise RuntimeError("initial Metal profile requires MTP speculative decoding")
    return document


def _runtime_python(config: dict[str, Any]) -> Path:
    return ROOT / str(config["runtime"]["environment"]) / "bin" / "python"


def _direct_packages(config: dict[str, Any]) -> dict[str, str]:
    return {
        name: version
        for package in config["runtime"]["packages"]
        for name, version in [str(package).split("==", 1)]
    }


def _interpreter_version(executable: Path) -> str:
    return subprocess.check_output(
        [str(executable), "-c", "import platform; print(platform.python_version())"],
        text=True,
    ).strip()


def _require_runtime_host(config: dict[str, Any]) -> None:
    actual = f"{platform.system()}/{platform.machine()}"
    if actual != "Darwin/arm64":
        raise RuntimeError(f"Metal runtime commands require Darwin/arm64; current host is {actual}")
    expected_python = str(config["runtime"]["python"])
    actual_python = _interpreter_version(_base_python())
    if actual_python != expected_python:
        raise RuntimeError(
            f"Metal runtime requires base Python {expected_python}; current base interpreter "
            f"{_base_python()} is {actual_python}"
        )


def _base_python() -> Path:
    return Path(getattr(sys, "_base_executable", None) or sys.executable).resolve()


def _profile(config: dict[str, Any]) -> dict[str, Any]:
    return config["profiles"][config["default_profile"]]


def _model_refs(config: dict[str, Any]) -> tuple[tuple[str, str], tuple[str, str]]:
    profile = _profile(config)
    assistant = profile["assistant"]
    return (
        (str(profile["model_id"]), str(profile["revision"])),
        (str(assistant["model_id"]), str(assistant["revision"])),
    )


def _model_aliases(config: dict[str, Any]) -> tuple[Path, Path]:
    directory = ROOT / ".runtime" / "metal" / "models"
    public_model = str(config["public_model"])
    return directory / public_model, directory / f"{public_model}-assistant"


def _replace_generated_alias(alias: Path, snapshot: Path) -> None:
    alias.parent.mkdir(parents=True, exist_ok=True)
    if alias.is_symlink():
        if alias.resolve() == snapshot.resolve():
            return
        alias.unlink()
    elif alias.exists():
        raise RuntimeError(f"generated model alias path is not a symlink: {alias}")
    alias.symlink_to(snapshot, target_is_directory=True)


def _run_runtime_python(config: dict[str, Any], code: str, *args: str) -> str:
    python = _runtime_python(config)
    if not python.is_file():
        raise RuntimeError("Metal environment is missing; run `make metal-setup` first")
    return subprocess.check_output([str(python), "-c", code, *args], text=True).strip()


def doctor(config: dict[str, Any]) -> None:
    _require_runtime_host(config)
    runtime = config["runtime"]
    profile = _profile(config)
    normal = profile["qualification"]["normal"]
    speculative = runtime["speculative_decoding"]
    print(f"[metal] host: Darwin/arm64")
    print(f"[metal] base Python: {_base_python()} ({_interpreter_version(_base_python())})")
    print(f"[metal] runtime packages: {', '.join(runtime['packages'])}")
    print(f"[metal] target: {profile['model_id']}@{profile['revision']}")
    print(f"[metal] assistant: {profile['assistant']['model_id']}@{profile['assistant']['revision']}")
    print(
        "[metal] profile: "
        f"input={normal['input_tokens']} generation={normal['generation_tokens']} "
        f"images={normal['images_min']}..{normal['images_max']} "
        f"MTP={'on' if speculative['enabled'] else 'off'} "
        f"TurboQuant={'on' if runtime['turboquant']['enabled'] else 'off'} "
        f"concurrency={runtime['max_concurrency']}"
    )


def lock(config: dict[str, Any]) -> None:
    _require_runtime_host(config)
    runtime = config["runtime"]
    output = ROOT / str(runtime["lock_file"])
    packages = [str(package) for package in runtime["packages"]]
    with tempfile.TemporaryDirectory(prefix="metal-lock-") as temporary_name:
        temporary = Path(temporary_name)
        tools = temporary / "tools"
        source = temporary / "requirements.in"
        generated = temporary / "requirements.lock"
        source.write_text("\n".join(packages) + "\n", encoding="utf-8")
        if output.is_file():
            # pip-compile은 기존 output pin을 입력으로 사용해 무관한 전이 의존성
            # upgrade를 피한다. upgrade는 별도 의사결정으로 남긴다.
            shutil.copyfile(output, generated)
        subprocess.run([str(_base_python()), "-m", "venv", str(tools)], check=True)
        pip = [str(tools / "bin" / "python"), "-m", "pip", "--disable-pip-version-check"]
        subprocess.run([*pip, "install", "--quiet", "pip==26.0.1", "pip-tools==7.5.3"], check=True)
        subprocess.run(
            [
                str(tools / "bin" / "pip-compile"),
                "--resolver=backtracking",
                "--quiet",
                "--strip-extras",
                "--no-annotate",
                "--no-emit-index-url",
                "--no-emit-trusted-host",
                "--output-file",
                str(generated),
                str(source),
            ],
            cwd=ROOT,
            check=True,
            env={**os.environ, "CUSTOM_COMPILE_COMMAND": "make metal-lock"},
        )
        os.replace(generated, output)
    print(f"[metal] lock updated: {output.relative_to(ROOT)}")


def setup(config: dict[str, Any]) -> None:
    _require_runtime_host(config)
    runtime = config["runtime"]
    lock_path = ROOT / str(runtime["lock_file"])
    if not lock_path.is_file():
        raise RuntimeError("Metal dependency lock is missing; run `make metal-lock` first")
    environment = ROOT / str(runtime["environment"])
    python = _runtime_python(config)
    if not python.is_file():
        subprocess.run([str(_base_python()), "-m", "venv", str(environment)], check=True)
    expected_python = str(runtime["python"])
    runtime_version = _interpreter_version(python)
    if runtime_version != expected_python:
        raise RuntimeError(
            f"existing Metal environment uses Python {runtime_version}, configured runtime requires "
            f"{expected_python}; move .runtime/metal/venv aside before rebuilding it"
        )
    pip = [str(python), "-m", "pip", "--disable-pip-version-check"]
    subprocess.run([*pip, "install", "--quiet", "pip==26.0.1"], check=True)
    subprocess.run([*pip, "install", "--quiet", "--requirement", str(lock_path)], check=True)
    subprocess.run([*pip, "check"], check=True)
    for package, expected in _direct_packages(config).items():
        installed = _run_runtime_python(
            config,
            "import importlib.metadata as m, sys; print(m.version(sys.argv[1]))",
            package,
        )
        if installed != expected:
            raise RuntimeError(f"expected {package} {expected}, installed {installed}")
    print(f"[metal] environment ready: {environment.relative_to(ROOT)}")


def prepare(config: dict[str, Any]) -> None:
    code = (
        "from huggingface_hub import snapshot_download; import sys; "
        "print(snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2]))"
    )
    snapshots: list[Path] = []
    for repo_id, revision in _model_refs(config):
        path = _run_runtime_python(config, code, repo_id, revision)
        snapshots.append(Path(path))
        print(f"[metal] cached {repo_id}@{revision}: {path}")
    for alias, snapshot in zip(_model_aliases(config), snapshots, strict=True):
        _replace_generated_alias(alias, snapshot)
        print(f"[metal] alias {alias.name} -> {snapshot}")


def _local_snapshot(config: dict[str, Any], repo_id: str, revision: str) -> str:
    code = (
        "from huggingface_hub import snapshot_download; import sys; "
        "print(snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], local_files_only=True))"
    )
    try:
        return _run_runtime_python(config, code, repo_id, revision)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"pinned snapshot is not cached: {repo_id}@{revision}; run `make metal-prepare`") from exc


def server_command(config: dict[str, Any], *, listen_host: str | None = None) -> list[str]:
    runtime = config["runtime"]
    target_ref, assistant_ref = _model_refs(config)
    target_snapshot = Path(_local_snapshot(config, *target_ref)).resolve()
    assistant_snapshot = Path(_local_snapshot(config, *assistant_ref)).resolve()
    target_alias, assistant_alias = _model_aliases(config)
    for alias, snapshot in ((target_alias, target_snapshot), (assistant_alias, assistant_snapshot)):
        if not alias.is_symlink() or alias.resolve() != snapshot:
            raise RuntimeError(f"model alias is missing or stale: {alias}; run `make metal-prepare`")
    executable = _runtime_python(config).parent / "mlx_vlm.server"
    if not executable.is_file():
        raise RuntimeError("mlx_vlm.server is missing; run `make metal-setup`")
    command = [
        str(executable),
        # start() changes cwd to the generated alias directory. Keeping the
        # target argument equal to the public model ID makes MLX's cache key,
        # /v1/models and response model field agree with the Gateway contract.
        "--model", target_alias.name,
        "--draft-model", assistant_alias.name,
        "--draft-kind", str(runtime["speculative_decoding"]["kind"]),
        "--draft-block-size", str(runtime["speculative_decoding"]["block_size"]),
        "--host", listen_host or str(runtime["host"]),
        "--port", str(runtime["port"]),
        "--max-tokens", str(runtime["max_generation_tokens"]),
        "--max-num-seqs", str(runtime["max_concurrency"]),
        "--max-kv-size", str(runtime["max_kv_size"]),
        "--vision-cache-size", str(runtime["vision_cache_size"]),
    ]
    if runtime["thinking"]["enabled_by_default"] is True:
        command.append("--enable-thinking")
    return command


def status(config: dict[str, Any]) -> None:
    runtime = config["runtime"]
    url = f"http://127.0.0.1:{runtime['port']}{runtime['health_path']}"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = response.read().decode("utf-8")
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Metal runtime is not ready at {url}: {exc}") from exc
    print(f"[metal] ready: {payload}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the pinned macOS MLX-VLM runtime.")
    parser.add_argument("action", choices=("doctor", "lock", "setup", "prepare", "command", "start", "status"))
    parser.add_argument("--listen-host", default=None)
    args = parser.parse_args(argv)
    try:
        config = _config()
        if args.action == "doctor":
            doctor(config)
        elif args.action == "lock":
            lock(config)
        elif args.action == "setup":
            setup(config)
        elif args.action == "prepare":
            prepare(config)
        elif args.action == "status":
            status(config)
        else:
            command = server_command(config, listen_host=args.listen_host)
            if args.action == "command":
                print(f"cd {shlex.quote(str(_model_aliases(config)[0].parent))} && {shlex.join(command)}")
            else:
                os.chdir(_model_aliases(config)[0].parent)
                os.execv(command[0], command)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[metal] fail: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
