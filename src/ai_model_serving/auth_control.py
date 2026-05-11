from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .settings import AppSettings


AUTH_MODE_EXPECTATIONS: dict[str, dict[str, bool | str]] = {
    "local_open": {
        "api_key_required": False,
        "admin_api_key_required": False,
        "admin_endpoints_internal_only": False,
        "internal_service_auth_required": False,
        "docs_enabled": True,
        "scope": "로컬 개발 전용",
    },
    "private_network": {
        "api_key_required": True,
        "admin_api_key_required": True,
        "admin_endpoints_internal_only": False,
        "internal_service_auth_required": True,
        "docs_enabled": True,
        "scope": "사설망 또는 VPN 경계 + app-level admin token",
    },
    "edge_terminated": {
        "api_key_required": False,
        "admin_api_key_required": True,
        "admin_endpoints_internal_only": False,
        "internal_service_auth_required": True,
        "docs_enabled": False,
        "scope": "edge proxy가 public 인증을 처리하고 app은 admin/internal 경로를 보호",
    },
    "strict": {
        "api_key_required": True,
        "admin_api_key_required": True,
        "admin_endpoints_internal_only": False,
        "internal_service_auth_required": True,
        "docs_enabled": False,
        "scope": "production 또는 internet-reachable 배포",
    },
    "custom": {
        "scope": "운영자가 직접 관리하는 custom flag 조합",
    },
}

AUTH_PROFILE_ENV_KEYS = (
    "AUTH_MODE",
    "API_KEY_REQUIRED",
    "ADMIN_API_KEY_REQUIRED",
    "ADMIN_ENDPOINTS_INTERNAL_ONLY",
    "INTERNAL_SERVICE_AUTH_REQUIRED",
    "FASTAPI_DOCS_ENABLED",
)


def auth_profile_env_values(mode: str) -> dict[str, str]:
    """Return concrete env flag values for a managed auth profile.

    Secrets are intentionally not included. This helper is used by setup_env,
    auth-plan/apply tooling, tests, and docs so profile behavior does not drift
    across operator UX surfaces.
    """
    expected = AUTH_MODE_EXPECTATIONS.get(mode)
    if expected is None or mode == "custom":
        raise ValueError(f"{mode!r} is not a managed auth profile")
    return {
        "AUTH_MODE": mode,
        "API_KEY_REQUIRED": str(bool(expected["api_key_required"])).lower(),
        "ADMIN_API_KEY_REQUIRED": str(bool(expected["admin_api_key_required"])).lower(),
        "ADMIN_ENDPOINTS_INTERNAL_ONLY": str(bool(expected["admin_endpoints_internal_only"])).lower(),
        "INTERNAL_SERVICE_AUTH_REQUIRED": str(bool(expected["internal_service_auth_required"])).lower(),
        "FASTAPI_DOCS_ENABLED": str(bool(expected["docs_enabled"])).lower(),
    }


def auth_profile_summary(mode: str) -> str:
    expected = AUTH_MODE_EXPECTATIONS.get(mode, AUTH_MODE_EXPECTATIONS["custom"])
    return str(expected.get("scope", "운영자가 직접 관리하는 custom flag 조합"))

NON_LOCAL_ENVS = {"staging", "production", "prod"}
PRIVATE_NETWORK_WARNING_PORTS = {"risk-adapter", "prometheus", "grafana", "cadvisor"}


@dataclass(frozen=True)
class AuthFinding:
    level: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "message": self.message}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _compose_host_port_services(project_root: Path) -> list[str]:
    compose = _read_yaml(project_root / "ops" / "compose" / "full-stack.example.yaml")
    services = compose.get("services", {})
    published: list[str] = []
    if not isinstance(services, dict):
        return published
    for name, cfg in services.items():
        if isinstance(cfg, dict) and cfg.get("ports"):
            published.append(str(name))
    return published


def auth_status_document(settings: AppSettings, project_root: Path, env_path: Path | None = None) -> dict[str, Any]:
    env_path = env_path or (project_root / ".env")
    published_services = _compose_host_port_services(project_root)
    return {
        "env_file": {
            "path": str(env_path),
            "exists": env_path.exists(),
            "repository_default": env_path.resolve() == (project_root / ".env").resolve(),
        },
        "auth_mode": settings.security.auth_mode,
        "app_env": settings.app_env,
        "mode_scope": AUTH_MODE_EXPECTATIONS.get(settings.security.auth_mode, AUTH_MODE_EXPECTATIONS["custom"]).get("scope", "unknown"),
        "public_api": {
            "/v1/*": "api_key_required" if settings.security.api_key_required else "unauthenticated",
            "/docs": "enabled" if settings.documentation.enabled else "disabled",
            "/openapi.json": "enabled" if settings.documentation.enabled else "disabled",
        },
        "admin_endpoints": {
            "/ready": "admin_key_required" if settings.security.admin_api_key_required else "no_app_token_required",
            "/metrics": "admin_key_required" if settings.security.admin_api_key_required else "no_app_token_required",
            "internal_only_declared": settings.security.admin_endpoints_internal_only,
            "app_level_cidr_enforcement": False,
        },
        "internal_services": {
            "gateway_to_risk_adapter": "internal_token_required" if settings.security.internal_service_auth_required else "unauthenticated",
            "risk_adapter_host_port_published": "risk-adapter" in published_services,
        },
        "observability": {
            "published_compose_services": [name for name in published_services if name in PRIVATE_NETWORK_WARNING_PORTS],
        },
    }


def diagnose_auth(settings: AppSettings, project_root: Path) -> list[AuthFinding]:
    findings: list[AuthFinding] = []
    mode = settings.security.auth_mode
    expected = AUTH_MODE_EXPECTATIONS.get(mode)
    if expected is None:
        findings.append(AuthFinding("WARN", "AUTH_MODE_UNKNOWN", f"AUTH_MODE={mode!r}는 알려진 profile이 아니므로 custom으로 해석합니다."))
        expected = AUTH_MODE_EXPECTATIONS["custom"]

    if mode != "custom":
        for key, actual in {
            "api_key_required": settings.security.api_key_required,
            "admin_api_key_required": settings.security.admin_api_key_required,
            "admin_endpoints_internal_only": settings.security.admin_endpoints_internal_only,
            "internal_service_auth_required": settings.security.internal_service_auth_required,
            "docs_enabled": settings.documentation.enabled,
        }.items():
            if key in expected and expected[key] != actual:
                findings.append(AuthFinding("WARN", "AUTH_MODE_FLAG_MISMATCH", f"AUTH_MODE={mode} 기대값은 {key}={expected[key]}인데 실제값은 {actual}입니다."))

    non_local = settings.app_env.lower() in NON_LOCAL_ENVS or settings.app_env.lower() not in {"local", "test", "development"}
    if non_local and not settings.security.api_key_required:
        findings.append(AuthFinding("FAIL", "PUBLIC_API_UNAUTHENTICATED_NON_LOCAL", f"APP_ENV={settings.app_env}인데 API_KEY_REQUIRED=false입니다."))
    if non_local and not settings.security.internal_service_auth_required:
        findings.append(AuthFinding("FAIL", "INTERNAL_SERVICE_AUTH_DISABLED_NON_LOCAL", f"APP_ENV={settings.app_env}인데 INTERNAL_SERVICE_AUTH_REQUIRED=false입니다."))
    if non_local and not settings.security.admin_api_key_required and not settings.security.admin_endpoints_internal_only:
        findings.append(AuthFinding("WARN", "ADMIN_ENDPOINTS_OPEN_NON_LOCAL", f"APP_ENV={settings.app_env}에서 ADMIN_API_KEY_REQUIRED=false 및 ADMIN_ENDPOINTS_INTERNAL_ONLY=false입니다."))
    if settings.security.admin_endpoints_internal_only and not settings.security.admin_api_key_required:
        findings.append(AuthFinding("WARN", "ADMIN_INTERNAL_ONLY_NOT_APP_ENFORCED", "ADMIN_ENDPOINTS_INTERNAL_ONLY=true는 배포/networking 선언이며 app-level CIDR enforcement는 아직 구현되지 않았습니다."))

    published = set(_compose_host_port_services(project_root))
    if "risk-adapter" in published:
        findings.append(AuthFinding("WARN", "RISK_ADAPTER_HOST_PORT_PUBLISHED", "reference compose 파일에서 Risk Adapter host port가 published 상태입니다. private network 또는 firewall로 보호하세요."))
    for service in sorted(published & {"prometheus", "grafana", "cadvisor"}):
        findings.append(AuthFinding("WARN", "OBSERVABILITY_HOST_PORT_PUBLISHED", f"{service} host port가 reference compose 파일에서 published 상태입니다. shared 환경에서는 접근을 제한하세요."))

    if not findings:
        findings.append(AuthFinding("OK", "AUTH_POLICY_OK", "인증 제어 플레인에서 발견된 문제가 없습니다."))
    return findings


def render_auth_status(settings: AppSettings, project_root: Path, env_path: Path | None = None) -> str:
    doc = auth_status_document(settings, project_root, env_path)
    lines = [
        f"인증 profile: {doc['auth_mode']}",
        f"APP_ENV: {doc['app_env']}",
        f"env 파일: {doc['env_file']['path'] if doc['env_file']['exists'] else str(doc['env_file']['path']) + ' (없음)'}",
        f"적용 범위: {doc['mode_scope']}",
        "",
        "Public API",
    ]
    for name, state in doc["public_api"].items():
        lines.append(f"  {name:<22} {state}")
    lines.extend(["", "Admin endpoint"])
    for name in ["/ready", "/metrics"]:
        lines.append(f"  {name:<22} {doc['admin_endpoints'][name]}")
    lines.append(f"  internal_only 선언    {doc['admin_endpoints']['internal_only_declared']}")
    lines.append(f"  app CIDR enforcement  {doc['admin_endpoints']['app_level_cidr_enforcement']}")
    lines.extend(["", "Internal service"])
    lines.append(f"  Gateway -> Risk Adapter {doc['internal_services']['gateway_to_risk_adapter']}")
    lines.append(f"  Risk Adapter host port  {'published' if doc['internal_services']['risk_adapter_host_port_published'] else 'not_published'}")
    lines.extend(["", "Observability"])
    published = doc["observability"]["published_compose_services"]
    lines.append(f"  Host-published service {', '.join(published) if published else 'none'}")
    env_info = doc["env_file"]
    if not env_info["exists"]:
        lines.extend([
            "",
            "안내",
            "  지정된 env 파일이 없어 기본 설정값으로 상태를 표시했습니다.",
            "  실제 운영 전에는 `make init-env-local` 또는 `make init-env-compose`로 env를 생성한 뒤 다시 확인하세요.",
        ])
    elif not env_info["repository_default"]:
        lines.extend([
            "",
            "안내",
            "  --env로 지정한 파일을 기준으로 인증 상태를 표시했습니다.",
            "  repository root .env에는 자동 반영하지 않습니다.",
        ])
    return "\n".join(lines) + "\n"


def render_auth_findings(findings: list[AuthFinding]) -> str:
    return "\n".join(f"{finding.level}: {finding.code}: {finding.message}" for finding in findings) + "\n"
