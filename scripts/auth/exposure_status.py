#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import yaml
except ModuleNotFoundError:
    raise SystemExit("Missing dependency: PyYAML. Run `python -m pip install --requirement requirements.lock`.")

# Import resolver so exposure_status uses the same supported-mode check as compose_up.sh.
sys.path.insert(0, str(ROOT))
from scripts.compose.resolve_exposure_mode import load_exposure_data, resolve  # noqa: E402
sys.path.insert(0, str(ROOT / "src"))
from ai_model_serving.settings_parts.dotenv_parser import load_strict_env_file  # noqa: E402


def _env_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _env_value(values: dict[str, str], key: str, default: str = "") -> str:
    return os.environ.get(key, values.get(key, default))


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="현재 EXPOSURE_MODE의 host-published 서비스와 diagnostics를 표시합니다.")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    parser.add_argument("--exposure-mode", help="점검할 EXPOSURE_MODE (기본값: 환경 변수 또는 master_open)")
    parser.add_argument("--env", default=".env", help="점검할 env 파일 경로입니다. 기본값은 repository root의 .env입니다.")
    args = parser.parse_args()

    env_path = _env_path(args.env)
    if env_path is not None and not env_path.exists():
        print(f"env 파일을 찾을 수 없습니다: {env_path}", file=sys.stderr)
        return 2
    try:
        env_values = load_strict_env_file(env_path) if env_path is not None else {}
    except RuntimeError as exc:
        print(f"env 파일 오류: {exc}", file=sys.stderr)
        return 2

    raw_mode = args.exposure_mode or _env_value(env_values, "EXPOSURE_MODE", "master_open")
    data = load_exposure_data(ROOT)
    profiles = data.get("profiles", {})
    services_path = ROOT / "configs" / "services.yaml"
    services = yaml.safe_load(services_path.read_text(encoding="utf-8")).get("services", {})

    canonical_mode = resolve(raw_mode, data)

    profile = profiles.get(canonical_mode, {})
    published_service_names = profile.get("host_published", [])
    diagnostics: dict = profile.get("diagnostics", {})

    audience = _env_value(env_values, "EXPOSURE_AUDIENCE", "")

    published_services_detail = []
    for svc_name in published_service_names:
        svc = services.get(svc_name, {})
        port_env = svc.get("host_env_port", "")
        bind_env = svc.get("host_env_bind", "")
        default_port = svc.get("default_host_port", "")
        default_bind = svc.get("default_bind", "0.0.0.0")
        actual_port = _env_value(env_values, port_env, str(default_port)) if port_env else str(default_port)
        actual_bind = _env_value(env_values, bind_env, default_bind) if bind_env else default_bind
        published_services_detail.append({
            "name": svc_name,
            "compose_service": svc.get("compose_service", svc_name),
            "container_port": svc.get("container_port", ""),
            "host_port": actual_port,
            "bind": actual_bind,
            "host_env_port": port_env,
            "host_env_bind": bind_env,
        })

    allowed_audiences: list[str] = data.get("exposure_audience", {}).get("allowed_values", [])
    allowed_str = "|".join(allowed_audiences) if allowed_audiences else "local_only|private_lan|vpn|public"

    remediation: list[str] = []
    if diagnostics.get("requires_exposure_audience") and not audience:
        remediation.append(f"EXPOSURE_AUDIENCE가 설정되지 않았습니다. EXPOSURE_AUDIENCE={allowed_str} 중 하나를 설정하세요.")
    elif diagnostics.get("requires_exposure_audience") and allowed_audiences and audience not in allowed_audiences:
        remediation.append(f"EXPOSURE_AUDIENCE={audience!r}는 유효하지 않은 값입니다. 허용값: {allowed_str}.")
    elif audience == "local_only" and diagnostics.get("requires_exposure_audience"):
        open_bind_svcs = []
        for svc in published_services_detail:
            if svc["bind"] == "0.0.0.0":
                open_bind_svcs.append(f"{svc['compose_service']} ({svc['host_env_bind'] or 'default'}=0.0.0.0)")
        if open_bind_svcs:
            preview = ", ".join(open_bind_svcs[:3])
            if len(open_bind_svcs) > 3:
                preview += f" ... ({len(open_bind_svcs)} total)"
            remediation.append(f"EXPOSURE_AUDIENCE=local_only인데 서비스가 0.0.0.0(전체 인터페이스)에 바인드됩니다: {preview}. *_BIND_ADDR=127.0.0.1로 설정하거나 EXPOSURE_AUDIENCE를 변경하세요.")
    if audience == "public" and diagnostics.get("direct_model_runtime_access"):
        remediation.append("EXPOSURE_AUDIENCE=public이고 vLLM direct access가 활성화됩니다. 인증 없이 모델 API가 노출되므로 firewall/VPN으로 보호해야 합니다.")
    if audience == "public" and diagnostics.get("direct_operations_endpoints"):
        remediation.append("EXPOSURE_AUDIENCE=public이고 Prometheus/DCGM/cAdvisor가 직접 노출됩니다. ALLOW_PUBLIC_OPERATIONS_ENDPOINTS=true를 명시적으로 설정하지 않으면 운영상 권장되지 않습니다.")

    doc = {
        "raw_mode": raw_mode,
        "canonical_mode": canonical_mode,
        "description": profile.get("description", ""),
        "exposure_audience": audience,
        "host_published_services": published_services_detail,
        "diagnostics": diagnostics,
        "remediation": remediation,
    }

    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return 0

    # human-readable output
    print(f"EXPOSURE_MODE: {canonical_mode}")

    print(f"설명: {profile.get('description', '').strip()}")
    print()

    audience_display = audience if audience else "(설정 안 됨)"
    print(f"EXPOSURE_AUDIENCE: {audience_display}")
    print()

    print("Host-published 서비스:")
    if published_services_detail:
        for svc in published_services_detail:
            print(f"  {svc['compose_service']:<25} {svc['bind']}:{svc['host_port']} -> container:{svc['container_port']}")
    else:
        print("  (없음 — 모든 서비스가 compose 내부망)")

    print()
    print("Diagnostics:")
    for key, val in diagnostics.items():
        marker = "true " if val else "false"
        print(f"  {key}: {marker}")

    if remediation:
        print()
        print("Remediation:")
        for r in remediation:
            print(f"  - {r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
