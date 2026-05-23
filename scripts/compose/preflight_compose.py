#!/usr/bin/env python3
"""Config-first full-stack compose preflight."""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(line_buffering=True)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.settings_parts.dotenv_parser import load_strict_env_file  # noqa: E402
from scripts.compose.effective_host_ports import effective_host_ports  # noqa: E402
from scripts.compose.resolve_exposure_mode import (  # noqa: E402
    load_exposure_data,
    override_file_for,
    resolve,
)


def _fail(message: str) -> None:
    print(f"[preflight] fail: {message}", file=sys.stderr)


def _warn(message: str) -> None:
    print(f"[preflight] warn: {message}", file=sys.stderr)


def _env_value(key: str, default: str = "") -> str:
    value = os.environ.get(key)
    if value is not None:
        return value
    env_file = Path(os.environ.get("ENV_FILE", ".env"))
    env_path = env_file if env_file.is_absolute() else ROOT / env_file
    if env_path.exists():
        try:
            return load_strict_env_file(env_path).get(key, default)
        except RuntimeError as exc:
            _fail(f"invalid env file: {env_path}")
            for line in str(exc).splitlines():
                print(f"  {line}", file=sys.stderr)
            print(
                "[preflight] Fix strict KEY=VALUE syntax before runtime checks. "
                "Duplicate keys, quotes, inline comments, and export syntax are not supported.",
                file=sys.stderr,
            )
            raise SystemExit("[preflight] configuration preflight failed; fix env file syntax.") from exc
    return default


def _non_local_app_env() -> bool:
    app_env = _env_value("APP_ENV", "local").strip().lower()
    return app_env in {"staging", "stage", "production", "prod"} or app_env not in {"local", "test", "development"}


def _check_auth_profile_preflight() -> None:
    app_env = _env_value("APP_ENV", "local").strip()
    auth_mode = _env_value("AUTH_MODE", "local_open").strip() or "local_open"
    if not _non_local_app_env():
        return
    failures: list[str] = []
    if auth_mode == "local_open":
        failures.append(f"AUTH_MODE=local_open is not allowed when APP_ENV={app_env}.")
    if auth_mode == "internal_trusted" and not _env_value("INTERNAL_TRUSTED_AUTH_EVIDENCE").strip():
        failures.append(
            "AUTH_MODE=internal_trusted requires INTERNAL_TRUSTED_AUTH_EVIDENCE "
            "describing the network/edge/caller auth owner."
        )
    if auth_mode == "custom":
        accepted = _env_value("CUSTOM_AUTH_RISK_ACCEPTED").lower() in {"1", "true"}
        ticket = _env_value("CUSTOM_AUTH_RISK_TICKET").strip()
        if not accepted or not ticket:
            failures.append(
                "AUTH_MODE=custom requires CUSTOM_AUTH_RISK_ACCEPTED=true and "
                "CUSTOM_AUTH_RISK_TICKET in non-local environments."
            )
    if failures:
        for failure in failures:
            _fail(failure)
        raise SystemExit("[preflight] configuration preflight failed; fix auth profile evidence before runtime checks.")
    print(f"[preflight] ok: AUTH_MODE={auth_mode} APP_ENV={app_env}")


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise SystemExit(f"[preflight] fail: cannot load {label}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"[preflight] fail: {label} must be a YAML mapping.")
    return data


def _phase0() -> dict[str, Any]:
    print("[preflight] Phase 0: exposure config bootstrap")
    exposure_path = ROOT / "configs" / "exposure_profiles.yaml"
    data = _load_yaml(exposure_path, "configs/exposure_profiles.yaml")
    if not isinstance(data.get("profiles"), dict):
        raise SystemExit("[preflight] fail: configs/exposure_profiles.yaml must contain a profiles mapping.")
    print("[preflight] ok: loaded configs/exposure_profiles.yaml")
    return data


def _service_registry() -> dict[str, dict[str, Any]]:
    data = _load_yaml(ROOT / "configs" / "services.yaml", "configs/services.yaml")
    services = data.get("services")
    if not isinstance(services, dict):
        raise SystemExit("[preflight] fail: configs/services.yaml must contain a services mapping.")
    return services


def _bind_conflicts(
    mode: str,
    exposure_data: dict[str, Any],
    services: dict[str, dict[str, Any]],
) -> list[str]:
    profile = exposure_data.get("profiles", {}).get(mode, {})
    conflicts: list[str] = []
    for service_name in profile.get("host_published", []):
        service = services.get(service_name, {})
        bind_env = str(service.get("host_env_bind", ""))
        default_bind = str(service.get("default_bind", "0.0.0.0"))
        bind = _env_value(bind_env, default_bind) if bind_env else default_bind
        if bind != "0.0.0.0":
            continue
        port_env = str(service.get("host_env_port", ""))
        port = _env_value(port_env, str(service.get("default_host_port", ""))) if port_env else ""
        conflicts.append(f"{service.get('compose_service', service_name)}:{port}")
    return conflicts


def _phase1(exposure_data: dict[str, Any]) -> str:
    print("[preflight] Phase 1: exposure decision")
    _check_auth_profile_preflight()
    raw_mode = _env_value("EXPOSURE_MODE", "private_network")
    canonical_mode = resolve(raw_mode, exposure_data)
    print(f"[preflight] EXPOSURE_MODE={canonical_mode}")

    profile = exposure_data.get("profiles", {}).get(canonical_mode, {})
    diagnostics = profile.get("diagnostics", {})
    if not diagnostics.get("requires_exposure_audience"):
        return canonical_mode

    audience = _env_value("EXPOSURE_AUDIENCE", "")
    allowed = exposure_data.get("exposure_audience", {}).get("allowed_values", [])
    allowed = allowed or ["local_only", "private_lan", "vpn", "public"]
    if not audience:
        _fail(f"EXPOSURE_MODE={canonical_mode} requires EXPOSURE_AUDIENCE.")
        print(
            "[preflight] Set EXPOSURE_AUDIENCE=local_only|private_lan|vpn|public "
            "to declare who can reach host-published ports.",
            file=sys.stderr,
        )
        raise SystemExit("[preflight] configuration preflight failed; fix exposure config before runtime checks.")
    if audience not in allowed:
        _fail(f"EXPOSURE_AUDIENCE={audience!r} is not a valid value.")
        print(
            "[preflight] Allowed values (from configs/exposure_profiles.yaml): " + ", ".join(allowed),
            file=sys.stderr,
        )
        raise SystemExit("[preflight] configuration preflight failed; fix exposure config before runtime checks.")

    print(f"[preflight] ok: EXPOSURE_AUDIENCE={audience}")
    if audience == "local_only":
        conflicts = _bind_conflicts(canonical_mode, exposure_data, _service_registry())
        if conflicts:
            _fail(
                "EXPOSURE_AUDIENCE=local_only but services bound to 0.0.0.0: "
                + ",".join(conflicts[:5])
            )
            print(
                "[preflight] Set *_BIND_ADDR=127.0.0.1 for all host-published services, "
                "or change EXPOSURE_AUDIENCE.",
                file=sys.stderr,
            )
            raise SystemExit("[preflight] configuration preflight failed; fix exposure config before runtime checks.")
        print("[preflight] ok: EXPOSURE_AUDIENCE=local_only - all host-published services bound to loopback")

    if audience == "public":
        opt_in = _env_value("ALLOW_PUBLIC_OPERATIONS_ENDPOINTS", "")
        if opt_in not in {"1", "true"}:
            _fail(
                "EXPOSURE_AUDIENCE=public requires "
                "ALLOW_PUBLIC_OPERATIONS_ENDPOINTS=true as explicit opt-in."
            )
            print(
                f"[preflight] EXPOSURE_MODE={canonical_mode} with public audience exposes "
                "vLLM APIs and operations endpoints without Gateway auth.",
                file=sys.stderr,
            )
            raise SystemExit("[preflight] configuration preflight failed; fix exposure config before runtime checks.")
        _warn(
            "EXPOSURE_AUDIENCE=public + ALLOW_PUBLIC_OPERATIONS_ENDPOINTS=true - "
            "vLLM and ops endpoints publicly reachable."
        )
    return canonical_mode


def _compose_command(compose_args: list[str], env_file: str, *args: str) -> list[str]:
    return ["docker", "compose", *compose_args, "--env-file", env_file, *args]


def _compose_file_dir(compose_file: str) -> Path:
    path = Path(compose_file)
    return path.parent if path.is_absolute() else (ROOT / path).parent


def _resolve_compose_relative_path(compose_file: str, raw: str) -> Path:
    path = Path(raw.removeprefix("./"))
    return path if Path(raw).is_absolute() else _compose_file_dir(compose_file) / path


def _run_status(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=capture,
        check=False,
    )


def _docker_compose_available() -> bool:
    if not shutil.which("docker"):
        print("[preflight] missing: docker", file=sys.stderr)
        return False
    print("[preflight] ok: docker found")
    result = _run_status(["docker", "compose", "version"], capture=True)
    if result.returncode != 0:
        print("[preflight] missing: docker compose plugin", file=sys.stderr)
        return False
    print("[preflight] ok: docker compose available")
    return True


def _show_gpu() -> None:
    if not shutil.which("nvidia-smi"):
        _warn("nvidia-smi not found; GPU/vLLM full-stack cannot be validated on this host")
        return
    print("[preflight] ok: nvidia-smi found")
    result = _run_status(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
        capture=True,
    )
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            print(f"[preflight] gpu: {line}")


def _compose_owned_ports(compose_args: list[str], env_file: str) -> set[str]:
    result = _run_status(_compose_command(compose_args, env_file, "ps", "--format", "{{.Ports}}"), capture=True)
    if result.returncode != 0:
        return set()
    return set(re.findall(r"(?:\d+\.\d+\.\d+\.\d+|0\.0\.0\.0):(\d+)", result.stdout))


def _port_available(host_port: str, bind: str) -> bool:
    sock = socket.socket()
    sock.settimeout(0.4)
    try:
        sock.bind((bind, int(host_port)))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def _effective_ports(compose_args: list[str], env_file: str) -> list[tuple[str, str, str]] | None:
    result = _run_status(_compose_command(compose_args, env_file, "config"), capture=True)
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        _fail(f"docker compose config could not resolve {' '.join(compose_args)} with --env-file {env_file}.")
        print(
            "[preflight] Fix compose env/file errors shown above before checking host port availability.",
            file=sys.stderr,
        )
        return None
    document = yaml.safe_load(result.stdout) or {}
    return list(effective_host_ports(document))


def _diagnostics(canonical_mode: str, exposure_data: dict[str, Any]) -> None:
    if canonical_mode == "private_network":
        return
    diagnostics = exposure_data.get("profiles", {}).get(canonical_mode, {}).get("diagnostics", {})
    enabled = [(key, value) for key, value in diagnostics.items() if value]
    if not enabled:
        return
    print(f"[preflight] EXPOSURE_MODE={canonical_mode} structured diagnostics:")
    for key, value in enabled:
        print(f"  [diagnostic] {key}: {value}")


def _phase2(canonical_mode: str, exposure_data: dict[str, Any]) -> int:
    print("[preflight] Phase 2: compose and runtime checks")
    compose_file = os.environ.get("COMPOSE_FILE", "ops/compose/full-stack.private-network.yaml")
    env_file = os.environ.get("ENV_FILE", ".env")
    override = override_file_for(canonical_mode)
    compose_args = ["-f", compose_file, *(["-f", override] if override else [])]
    fail = False
    if not (ROOT / compose_file).exists() and not Path(compose_file).is_absolute():
        print(f"[preflight] missing: base compose file {compose_file}", file=sys.stderr)
        fail = True
    elif override and not (ROOT / override).exists():
        print(f"[preflight] missing: exposure override {override}", file=sys.stderr)
        print(
            "[preflight] Run 'python scripts/compose/render_exposure_overrides.py' and re-run preflight.",
            file=sys.stderr,
        )
        fail = True
    else:
        print("[preflight] ok: compose file set " + " ".join(compose_args))

    docker_ok = _docker_compose_available()
    fail = fail or not docker_ok
    _show_gpu()
    vllm = _run_status([sys.executable, "scripts/compose/validate_vllm_compose.py"])
    fail = fail or vllm.returncode != 0

    if docker_ok and not fail:
        owned_ports = _compose_owned_ports(compose_args, env_file)
        host_ports = _effective_ports(compose_args, env_file)
        if host_ports is None:
            fail = True
        elif not host_ports:
            print("[preflight] ok: effective compose config has no host-published ports")
        else:
            for service_name, host_port, bind in host_ports:
                if host_port in owned_ports:
                    print(f"[preflight] ok: port {host_port} ({service_name}) held by current compose stack")
                elif _port_available(host_port, bind):
                    print(f"[preflight] ok: port {host_port} ({service_name}) available on {bind}")
                else:
                    print(
                        f"[preflight] busy: port {host_port} ({service_name}) is already in use "
                        f"on {bind} (EXPOSURE_MODE={canonical_mode})",
                        file=sys.stderr,
                    )
                    fail = True
    elif not fail:
        _fail("cannot resolve effective compose ports without docker compose.")
        fail = True

    _diagnostics(canonical_mode, exposure_data)
    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"):
        print("[preflight] ok: Hugging Face token env present")
    else:
        _warn("no HF_TOKEN/HUGGING_FACE_HUB_TOKEN found; private model pulls may fail")

    cache_raw = os.environ.get("HF_CACHE_DIR", "./model_cache/huggingface")
    cache_path = _resolve_compose_relative_path(compose_file, cache_raw)
    cache_path.mkdir(parents=True, exist_ok=True)
    if cache_path.is_dir() and os.access(cache_path, os.W_OK):
        print(f"[preflight] ok: HF cache dir writable: {cache_path}")
        print(f"[preflight] relative HF_CACHE_DIR values are resolved from compose file directory: {_compose_file_dir(compose_file)}")
    else:
        print(f"[preflight] missing: HF cache dir is not writable: {cache_path}", file=sys.stderr)
        fail = True

    if os.environ.get("SKIP_RISK_VLLM_IMAGE_CONFIG_CHECK", "0") != "1":
        risk = _run_status(["bash", "scripts/models/check_risk_vllm_image_config.sh"])
        if risk.returncode == 0:
            print("[preflight] ok: risk vLLM image loads Kanana HF configs")
        else:
            print(
                "[preflight] risk vLLM image config check failed; build/check a Kanana-compatible "
                "RISK_VLLM_IMAGE or set SKIP_RISK_VLLM_IMAGE_CONFIG_CHECK=1 only for non-runtime local checks",
                file=sys.stderr,
            )
            fail = True
    else:
        print(
            "[preflight] skip: risk vLLM image config check disabled by "
            "SKIP_RISK_VLLM_IMAGE_CONFIG_CHECK=1",
            file=sys.stderr,
        )

    token_file = ROOT / ".runtime" / "prometheus" / "admin_api_key"
    if token_file.is_file() and token_file.stat().st_size > 0:
        print("[preflight] ok: Prometheus admin bearer-token file present")
    else:
        print(
            "[preflight] missing or invalid: .runtime/prometheus/admin_api_key must be a non-empty "
            "file; run 'make sync-runtime-secrets'",
            file=sys.stderr,
        )
        fail = True

    if fail:
        print(
            "[preflight] full-stack compose preflight failed; fix the items above before 'make compose-up'.",
            file=sys.stderr,
        )
        return 1
    print("[preflight] full-stack compose preflight passed")
    return 0


def main() -> int:
    exposure_data = _phase0()
    canonical_mode = _phase1(exposure_data)
    return _phase2(canonical_mode, exposure_data)


if __name__ == "__main__":
    raise SystemExit(main())
