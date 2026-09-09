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
import signal
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "macos_mlx_runtime.yaml"
PID_PATH = ROOT / "run" / "metal.pid"
LOG_PATH = ROOT / "logs" / "metal.log"


def _config() -> dict[str, Any]:
    document = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("version") != 1:
        raise RuntimeError(f"invalid Metal runtime config: {CONFIG_PATH}")
    runtime = document.get("runtime")
    profiles = document.get("profiles")
    default_profile = document.get("default_profile")
    if not isinstance(runtime, dict) or not isinstance(profiles, dict) or default_profile not in profiles:
        raise RuntimeError(f"incomplete Metal runtime config: {CONFIG_PATH}")
    project = runtime.get("project")
    if not isinstance(project, str) or not (ROOT / project / "pyproject.toml").is_file():
        raise RuntimeError("Metal runtime project must point to a Python project")
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


def _runtime_project(config: dict[str, Any]) -> Path:
    return ROOT / str(config["runtime"]["project"])


def _direct_packages(config: dict[str, Any]) -> list[str]:
    metadata = tomllib.loads(
        (_runtime_project(config) / "pyproject.toml").read_text(encoding="utf-8")
    )
    return [str(package) for package in metadata["project"]["dependencies"]]


def _uv_binary() -> str:
    configured = os.environ.get("UV_BIN")
    executable = configured or shutil.which("uv")
    if not executable:
        raise RuntimeError(
            "uv is required to prepare Python environments; install the version "
            "declared by runtimes/mlx/pyproject.toml "
            "or set UV_BIN=/path/to/uv"
        )
    return executable


def _interpreter_version(executable: Path) -> str:
    return subprocess.check_output(
        [str(executable), "-c", "import platform; print(platform.python_version())"],
        text=True,
    ).strip()


def _base_python_candidates(config: dict[str, Any]) -> list[Path]:
    expected_minor = ".".join(str(config["runtime"]["python"]).split(".")[:2])
    raw = [
        os.environ.get("METAL_PYTHON_BIN"),
        str(Path(getattr(sys, "_base_executable", None) or sys.executable)),
        shutil.which(f"python{expected_minor}"),
        f"/opt/homebrew/bin/python{expected_minor}",
        f"/usr/local/bin/python{expected_minor}",
    ]
    candidates: list[Path] = []
    for item in raw:
        if not item:
            continue
        candidate = Path(item).expanduser()
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _base_python(config: dict[str, Any]) -> Path:
    expected = str(config["runtime"]["python"])
    for candidate in _base_python_candidates(config):
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        try:
            if _interpreter_version(candidate) == expected:
                return candidate.resolve()
        except (OSError, subprocess.CalledProcessError):
            continue
    raise RuntimeError(
        f"Metal runtime requires Python {expected}; install that patch or set "
        "METAL_PYTHON_BIN=/path/to/python"
    )


def _require_runtime_host(config: dict[str, Any]) -> Path:
    actual = f"{platform.system()}/{platform.machine()}"
    if actual != "Darwin/arm64":
        raise RuntimeError(f"Metal runtime commands require Darwin/arm64; current host is {actual}")
    return _base_python(config)


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
        raise RuntimeError("Metal environment is missing; run `make setup TARGET=macos-metal-static` first")
    return subprocess.check_output([str(python), "-c", code, *args], text=True).strip()


def doctor(config: dict[str, Any]) -> None:
    base_python = _require_runtime_host(config)
    runtime = config["runtime"]
    profile = _profile(config)
    normal = profile["qualification"]["normal"]
    speculative = runtime["speculative_decoding"]
    print(f"[metal] host: Darwin/arm64")
    print(f"[metal] base Python: {base_python} ({_interpreter_version(base_python)})")
    print(f"[metal] runtime packages: {', '.join(_direct_packages(config))}")
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


def setup(config: dict[str, Any]) -> None:
    base_python = _require_runtime_host(config)
    runtime = config["runtime"]
    project = _runtime_project(config)
    lock_path = project / "uv.lock"
    if not lock_path.is_file():
        raise RuntimeError("Metal dependency lock is missing; run `make lock` first")
    environment = ROOT / str(runtime["environment"])
    python = _runtime_python(config)
    if environment.exists():
        if not (environment / "pyvenv.cfg").is_file() or not python.is_file():
            raise RuntimeError("existing Metal environment is not a usable virtual environment")
        expected_python = str(runtime["python"])
        runtime_version = _interpreter_version(python)
        if runtime_version != expected_python:
            raise RuntimeError(
                f"existing Metal environment uses Python {runtime_version}, configured runtime requires "
                f"{expected_python}; move .runtime/metal/venv aside before rebuilding it"
            )
    subprocess.run(
        [
            _uv_binary(),
            "sync",
            "--project",
            str(project),
            "--locked",
            "--python",
            str(base_python),
        ],
        cwd=ROOT,
        env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment)},
        check=True,
    )
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
        raise RuntimeError(f"pinned snapshot is not cached: {repo_id}@{revision}; run `make prepare`") from exc


def server_command(config: dict[str, Any], *, listen_host: str | None = None) -> list[str]:
    runtime = config["runtime"]
    target_ref, assistant_ref = _model_refs(config)
    target_snapshot = Path(_local_snapshot(config, *target_ref)).resolve()
    assistant_snapshot = Path(_local_snapshot(config, *assistant_ref)).resolve()
    target_alias, assistant_alias = _model_aliases(config)
    for alias, snapshot in ((target_alias, target_snapshot), (assistant_alias, assistant_snapshot)):
        if not alias.is_symlink() or alias.resolve() != snapshot:
            raise RuntimeError(f"model alias is missing or stale: {alias}; run `make prepare`")
    executable = _runtime_python(config).parent / "mlx_vlm.server"
    if not executable.is_file():
        raise RuntimeError("mlx_vlm.server is missing; run `make setup TARGET=macos-metal-static`")
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


def _tracked_pid() -> int | None:
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        PID_PATH.unlink(missing_ok=True)
        return None
    except PermissionError:
        return None
    return pid


def _health_payload(config: dict[str, Any]) -> str:
    runtime = config["runtime"]
    url = f"http://127.0.0.1:{runtime['port']}{runtime['health_path']}"
    with urllib.request.urlopen(url, timeout=3) as response:
        return response.read().decode("utf-8")


def start_background(config: dict[str, Any], *, listen_host: str = "0.0.0.0") -> None:
    pid = _tracked_pid()
    launched = False
    if pid is not None:
        print(f"[metal] runtime process already tracked: pid={pid}")
    else:
        try:
            payload = _health_payload(config)
        except (OSError, urllib.error.URLError):
            command = server_command(config, listen_host=listen_host)
            PID_PATH.parent.mkdir(parents=True, exist_ok=True)
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open("a", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command,
                    cwd=_model_aliases(config)[0].parent,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            PID_PATH.write_text(f"{process.pid}\n", encoding="utf-8")
            pid = process.pid
            launched = True
            print(f"[metal] runtime starting: pid={pid} log={LOG_PATH.relative_to(ROOT)}")
        else:
            print(f"[metal] runtime already ready but is not managed by this project: {payload}")
            return

    timeout = int(os.environ.get("METAL_START_TIMEOUT_SECONDS", "1800"))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _tracked_pid() is None:
            raise RuntimeError(f"Metal runtime exited before readiness; inspect {LOG_PATH}")
        try:
            payload = _health_payload(config)
        except (OSError, urllib.error.URLError):
            time.sleep(2)
            continue
        print(f"[metal] ready: {payload}")
        return
    if launched:
        try:
            stop_background()
        except (OSError, RuntimeError) as exc:
            print(f"[metal] cleanup after startup timeout failed: {exc}", file=sys.stderr)
    raise RuntimeError(f"Metal runtime did not become ready within {timeout}s; inspect {LOG_PATH}")


def stop_background() -> None:
    pid = _tracked_pid()
    if pid is None:
        print("[metal] no managed runtime process")
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            PID_PATH.unlink(missing_ok=True)
            print(f"[metal] runtime stopped: pid={pid}")
            return
        time.sleep(0.25)
    raise RuntimeError(
        f"Metal runtime pid {pid} did not stop within 30s; inspect it before retrying"
    )


def status(config: dict[str, Any]) -> None:
    try:
        payload = _health_payload(config)
    except (OSError, urllib.error.URLError) as exc:
        runtime = config["runtime"]
        raise RuntimeError(
            f"Metal runtime is not ready at http://127.0.0.1:{runtime['port']}{runtime['health_path']}: {exc}"
        ) from exc
    pid = _tracked_pid()
    suffix = f" pid={pid}" if pid is not None else " unmanaged"
    print(f"[metal] ready:{suffix} {payload}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the pinned macOS MLX-VLM runtime.")
    parser.add_argument(
        "action",
        choices=(
            "doctor", "setup", "prepare", "command", "start",
            "start-background", "stop", "status",
        ),
    )
    parser.add_argument("--listen-host", default=None)
    args = parser.parse_args(argv)
    try:
        config = _config()
        if args.action == "doctor":
            doctor(config)
        elif args.action == "setup":
            setup(config)
        elif args.action == "prepare":
            prepare(config)
        elif args.action == "status":
            status(config)
        elif args.action == "start-background":
            start_background(config, listen_host=args.listen_host or "0.0.0.0")
        elif args.action == "stop":
            stop_background()
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
