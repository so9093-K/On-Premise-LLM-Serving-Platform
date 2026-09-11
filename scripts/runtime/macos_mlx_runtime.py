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
import plistlib
import signal
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "macos_mlx_runtime.yaml"
PID_PATH = ROOT / "run" / "metal.pid"
# native runtime 로그는 runtime state에 둔다. 저장소의 logs/는 app-only 모드가
# gateway.log/risk_adapter.log를 쓰는 곳이라(`make logs`가 그걸 tail한다) 성격이 다르다.
NATIVE_LOG_DIR = ROOT / ".runtime" / "metal" / "logs"
# 수명주기 소유자마다 자기 파일을 갖는다. 하나를 공유하면 "누가 먼저 만들었는가"가
# 동작을 가른다 -- TCC 보호 경로(~/Desktop 등)에서 launchd는 자기가 만들어
# com.apple.macl이 붙은 파일만 열 수 있고, 다른 프로세스가 먼저 만든 파일에서는
# job이 EX_CONFIG(78)로 죽으면서 로그를 한 줄도 남기지 않는다. 경로를 나누면
# 그 상태 자체가 생길 수 없다.
LOG_PATH = NATIVE_LOG_DIR / "runtime.log"
SUPERVISOR_LOG_PATH = NATIVE_LOG_DIR / "supervisor.log"
# launchd label은 사용자 domain에서 전역이다. checkout이 여러 개면 서로를 덮어쓸 수
# 있으므로 install이 다른 checkout 소유를 감지하면 거부한다.
SUPERVISOR_LABEL = "com.ai-model-serving.metal"
# 생성된 plist는 절대 경로와 resolved snapshot을 담는 host state다. 저장소에 커밋하면
# 영구히 drift하는 생성물이 되므로 .runtime/ 아래에만 둔다(gitignore + make clean 범위).
SUPERVISOR_PLIST = ROOT / ".runtime" / "metal" / f"{SUPERVISOR_LABEL}.plist"


# 진단 시점에 보여줄 로그 분량. 파일 전체를 읽지 않고 끝에서부터 제한된 바이트만
# 읽는다 -- 이 로그는 회전이 없는 append 파일이라 크기를 가정할 수 없다.
_LOG_TAIL_LINES = 40
_LOG_TAIL_BYTES = 64 * 1024


def active_log_path() -> Path:
    """지금 runtime을 소유한 쪽이 쓰는 로그 파일."""
    return SUPERVISOR_LOG_PATH if supervisor_installed() else LOG_PATH


def _log_tail() -> list[str]:
    path = active_log_path()
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _LOG_TAIL_BYTES:
                handle.seek(size - _LOG_TAIL_BYTES)
                handle.readline()  # 잘린 첫 줄은 버린다
            data = handle.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-_LOG_TAIL_LINES:]


def _report_log_tail() -> None:
    """runtime이 죽었거나 안 뜰 때 원인을 그 자리에서 보여준다.

    예전에는 "inspect {LOG_PATH}"라고만 안내해서, 운영자가 경로를 알아내 따로
    열어야 원인을 볼 수 있었다. Linux에서는 `docker logs` 한 번이면 되는 일이다.
    """
    path = active_log_path()
    lines = _log_tail()
    if not lines:
        print(f"[metal] no runtime log yet: {path}", file=sys.stderr)
        return
    print(f"[metal] last {len(lines)} lines of {path}:", file=sys.stderr)
    for line in lines:
        print(f"  | {line}", file=sys.stderr)


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


def _launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def _supervisor_job() -> str:
    return f"{_launchctl_domain()}/{SUPERVISOR_LABEL}"


def _installed_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{SUPERVISOR_LABEL}.plist"


def supervisor_installed() -> bool:
    return _installed_plist().is_file()


def _supervisor_loaded() -> bool:
    return subprocess.run(
        ["launchctl", "print", _supervisor_job()],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0


def _launchctl(*args: str) -> None:
    result = subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"launchctl {' '.join(args)} failed: {detail or result.returncode}")


def _supervisor_document(config: dict[str, Any], *, listen_host: str) -> dict[str, Any]:
    """launchd plist를 server_command에서 만든다.

    손으로 쓰면 model revision, port, venv 경로, listen host를
    configs/macos_mlx_runtime.yaml에서 복제하게 된다. 실행 명령의 단일 기준은
    server_command 하나이며 plist는 그 결과를 감싸기만 한다.
    """
    return {
        "Label": SUPERVISOR_LABEL,
        "ProgramArguments": server_command(config, listen_host=listen_host),
        "WorkingDirectory": str(_model_aliases(config)[0].parent),
        "RunAtLoad": True,
        # Compose의 restart: unless-stopped에 대응한다. 의도적 정지는 SIGTERM이
        # 아니라 bootout이며, stop_background가 그 경로를 쓴다.
        "KeepAlive": True,
        # launchd만 이 파일을 만들고 쓴다. 다른 프로세스가 먼저 만드는 일이
        # 없으므로 TCC 보호 경로에서도 항상 자기 파일을 연다.
        "StandardOutPath": str(SUPERVISOR_LOG_PATH),
        "StandardErrorPath": str(SUPERVISOR_LOG_PATH),
        # 요청을 처리하는 서버이므로 launchd가 background 작업으로 낮춰 잡지 않게 한다.
        "ProcessType": "Interactive",
    }


def install_supervisor(config: dict[str, Any], *, listen_host: str) -> None:
    _require_runtime_host(config)
    if _tracked_pid() is not None:
        raise RuntimeError(
            "a runtime started by this project is still tracked in run/metal.pid; "
            "run `stop` first so launchd does not start a second server on the same port"
        )
    installed = _installed_plist()
    if installed.is_file():
        existing = plistlib.loads(installed.read_bytes())
        owner = str(existing.get("WorkingDirectory", ""))
        if owner and not owner.startswith(str(ROOT)):
            raise RuntimeError(
                f"{SUPERVISOR_LABEL} is already installed for another checkout ({owner}); "
                "uninstall it there before installing here"
            )
    document = _supervisor_document(config, listen_host=listen_host)
    SUPERVISOR_PLIST.parent.mkdir(parents=True, exist_ok=True)
    NATIVE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with SUPERVISOR_PLIST.open("wb") as handle:
        plistlib.dump(document, handle)
    installed.parent.mkdir(parents=True, exist_ok=True)
    if _supervisor_loaded():
        _launchctl("bootout", _supervisor_job())
    installed.write_bytes(SUPERVISOR_PLIST.read_bytes())
    _launchctl("bootstrap", _launchctl_domain(), str(installed))
    print(f"[metal] supervisor installed: label={SUPERVISOR_LABEL} plist={installed}")
    try:
        _await_readiness(config, is_alive=_supervisor_loaded, stop_on_timeout=False)
    except (OSError, RuntimeError):
        # KeepAlive는 기동에 실패한 job도 계속 되살린다. 설치가 성공하지 못했는데
        # plist를 남기면 crash loop가 백그라운드에 남고 재부팅마다 되살아난다.
        # 실측에서 launchd가 job을 띄우지 못한 채 18회 재시도한 적이 있다.
        print("[metal] supervisor did not become ready; removing the installation", file=sys.stderr)
        uninstall_supervisor()
        raise


def uninstall_supervisor() -> None:
    installed = _installed_plist()
    if not installed.is_file():
        print("[metal] supervisor is not installed")
        return
    if _supervisor_loaded():
        _launchctl("bootout", _supervisor_job())
    installed.unlink()
    SUPERVISOR_PLIST.unlink(missing_ok=True)
    print(f"[metal] supervisor removed: label={SUPERVISOR_LABEL}")


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


def _await_readiness(
    config: dict[str, Any], *, is_alive: Callable[[], bool], stop_on_timeout: bool
) -> None:
    """기동을 누가 소유하든 같은 readiness 판정을 쓴다.

    소유자별로 판정 루프를 복제하면 한쪽만 고쳐지는 drift가 생긴다. 달라지는 것은
    "아직 살아 있는가"를 무엇으로 확인하는지(``is_alive``)와 timeout 시 정리
    책임이 누구에게 있는지 뿐이다.
    """
    timeout = int(os.environ.get("METAL_START_TIMEOUT_SECONDS", "1800"))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_alive():
            _report_log_tail()
            raise RuntimeError(f"Metal runtime exited before readiness; inspect {active_log_path()}")
        try:
            payload = _health_payload(config)
        except (OSError, urllib.error.URLError):
            time.sleep(2)
            continue
        print(f"[metal] ready: {payload}")
        return
    if stop_on_timeout:
        try:
            stop_background()
        except (OSError, RuntimeError) as exc:
            print(f"[metal] cleanup after startup timeout failed: {exc}", file=sys.stderr)
    _report_log_tail()
    raise RuntimeError(f"Metal runtime did not become ready within {timeout}s; inspect {active_log_path()}")


def _start_supervised(config: dict[str, Any]) -> None:
    try:
        payload = _health_payload(config)
    except (OSError, urllib.error.URLError):
        pass
    else:
        print(f"[metal] already ready under launchd: {payload}")
        return
    if _supervisor_loaded():
        _launchctl("kickstart", "-k", _supervisor_job())
    else:
        _launchctl("bootstrap", _launchctl_domain(), str(_installed_plist()))
    print(f"[metal] runtime starting under launchd: label={SUPERVISOR_LABEL} log={SUPERVISOR_LOG_PATH.relative_to(ROOT)}")
    # timeout 정리를 하지 않는다. launchd가 KeepAlive로 다시 올릴 것이고, 느린 첫
    # 모델 적재를 실패로 단정해 supervisor를 내리는 것은 설치 의도에 반한다.
    _await_readiness(config, is_alive=_supervisor_loaded, stop_on_timeout=False)


def start_background(config: dict[str, Any], *, listen_host: str = "0.0.0.0") -> None:
    # 수명주기 소유자는 언제나 하나다. launchd가 설치돼 있으면 pid 파일 경로를
    # 쓰지 않는다. 둘을 병존시키면 같은 포트에 서버가 둘 뜨거나, 한쪽이 내린
    # 프로세스를 다른 쪽이 되살린다.
    if supervisor_installed():
        _start_supervised(config)
        return
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
            NATIVE_LOG_DIR.mkdir(parents=True, exist_ok=True)
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

    _await_readiness(
        config, is_alive=lambda: _tracked_pid() is not None, stop_on_timeout=launched
    )


def stop_background() -> None:
    if supervisor_installed():
        # KeepAlive가 SIGTERM을 즉시 되살리므로 bootout만이 실제 정지다. plist는
        # 남겨 다음 start가 재사용한다. 영구 제거는 uninstall-supervisor가 소유한다.
        if _supervisor_loaded():
            _launchctl("bootout", _supervisor_job())
            print(f"[metal] runtime stopped under launchd: label={SUPERVISOR_LABEL}")
        else:
            print(f"[metal] launchd job is not loaded: label={SUPERVISOR_LABEL}")
        return
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


def _ownership() -> str:
    if supervisor_installed():
        loaded = "loaded" if _supervisor_loaded() else "installed, not loaded"
        return f" supervisor={SUPERVISOR_LABEL} ({loaded})"
    pid = _tracked_pid()
    return f" pid={pid}" if pid is not None else " unmanaged"


def status(config: dict[str, Any]) -> None:
    try:
        payload = _health_payload(config)
    except (OSError, urllib.error.URLError) as exc:
        runtime = config["runtime"]
        # 운영자가 원인을 보러 오는 지점이 바로 여기다. 경로만 알려주고 끝내지 않는다.
        _report_log_tail()
        raise RuntimeError(
            f"Metal runtime is not ready at http://127.0.0.1:{runtime['port']}{runtime['health_path']}: {exc}"
        ) from exc
    print(f"[metal] ready:{_ownership()} {payload}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the pinned macOS MLX-VLM runtime.")
    parser.add_argument(
        "action",
        choices=(
            "doctor", "setup", "prepare", "command", "start",
            "start-background", "stop", "status",
            "install-supervisor", "uninstall-supervisor",
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
        elif args.action == "install-supervisor":
            install_supervisor(config, listen_host=args.listen_host or "0.0.0.0")
        elif args.action == "uninstall-supervisor":
            uninstall_supervisor()
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
