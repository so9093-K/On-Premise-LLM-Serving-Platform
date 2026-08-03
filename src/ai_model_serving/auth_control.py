from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .settings import AppSettings
from .settings_parts.env import env as _env


# ---------------------------------------------------------------------------
# YAML에서 auth 프로필을 로드한다 — configs/auth_profiles.yaml이 source of truth다.
# AUTH_MODE_EXPECTATIONS는 모듈 시작 시 이 YAML에서 파생되며, 수동으로
# 관리하는 dict가 아니다. auth 의미론 변경은 반드시 YAML을 먼저 수정한다.
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_AUTH_PROFILES_YAML = _PROJECT_ROOT / "configs" / "auth_profiles.yaml"

_BOOL_FIELDS = (
    "api_key_required",
    "admin_api_key_required",
    "admin_endpoints_internal_only",
    "internal_service_auth_required",
    "docs_enabled",
)

_SEMANTIC_FIELDS = (
    "auth_owner",
    "scope",
    "allowed_in_production",
)

_OPTIONAL_POLICY_FIELDS = (
    "default_exposure_mode",
    "default_exposure_audience",
)

_REQUIRED_FIELDS = _BOOL_FIELDS + _SEMANTIC_FIELDS


def _load_auth_profiles(yaml_path: Path) -> dict[str, dict[str, Any]]:
    """`configs/auth_profiles.yaml`을 읽어 인증 프로필 매핑을 반환한다."""
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"configs/auth_profiles.yaml not found at {yaml_path}. "
            "This file is the source of truth for auth mode expectations."
        )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"configs/auth_profiles.yaml is not a valid YAML mapping: {yaml_path}")
    return data.get("profiles", {})


def _build_expectations(profiles: dict[str, dict[str, Any]]) -> dict[str, dict[str, bool | str]]:
    """YAML 프로필을 ``AUTH_MODE_EXPECTATIONS`` 형식으로 변환한다."""
    result: dict[str, dict[str, bool | str]] = {}
    for mode, profile in profiles.items():
        entry: dict[str, bool | str] = {}
        for field in _BOOL_FIELDS:
            if field in profile:
                entry[field] = bool(profile[field])
        for field in (*_SEMANTIC_FIELDS, *_OPTIONAL_POLICY_FIELDS):
            if field in profile:
                val = profile[field]
                entry[field] = bool(val) if isinstance(val, bool) else str(val)
        result[mode] = entry
    return result


_YAML_PROFILES: dict[str, dict[str, Any]] = _load_auth_profiles(_AUTH_PROFILES_YAML)
AUTH_MODE_EXPECTATIONS: dict[str, dict[str, bool | str]] = _build_expectations(_YAML_PROFILES)

AUTH_PROFILE_ENV_KEYS = (
    "AUTH_MODE",
    "API_KEY_REQUIRED",
    "ADMIN_API_KEY_REQUIRED",
    "ADMIN_ENDPOINTS_INTERNAL_ONLY",
    "INTERNAL_SERVICE_AUTH_REQUIRED",
    "FASTAPI_DOCS_ENABLED",
)


def auth_profile_env_values(mode: str) -> dict[str, str]:
    """관리되는 인증 프로필에 대응하는 구체적인 환경 변수 값을 반환한다.

    Secrets are intentionally not included. This helper is used by setup_env,
    auth-plan/apply tooling, tests, and docs so profile behavior does not drift
    across operator UX surfaces.
    """
    expected = AUTH_MODE_EXPECTATIONS.get(mode)
    if expected is None or mode == "custom":
        raise ValueError(f"{mode!r} is not a managed auth profile")
    return {
        "AUTH_MODE": mode,
        "API_KEY_REQUIRED": str(bool(expected.get("api_key_required", False))).lower(),
        "ADMIN_API_KEY_REQUIRED": str(bool(expected.get("admin_api_key_required", False))).lower(),
        "ADMIN_ENDPOINTS_INTERNAL_ONLY": str(bool(expected.get("admin_endpoints_internal_only", False))).lower(),
        "INTERNAL_SERVICE_AUTH_REQUIRED": str(bool(expected.get("internal_service_auth_required", False))).lower(),
        "FASTAPI_DOCS_ENABLED": str(bool(expected.get("docs_enabled", True))).lower(),
    }


def auth_profile_exposure_values(mode: str) -> dict[str, str]:
    """인증 프로필이 직접 소유하는 기본 exposure 값을 반환한다."""
    expected = AUTH_MODE_EXPECTATIONS.get(mode)
    if expected is None or mode == "custom":
        raise ValueError(f"{mode!r} is not a managed auth profile")
    exposure_mode = expected.get("default_exposure_mode")
    if not exposure_mode:
        return {}
    values = {"EXPOSURE_MODE": str(exposure_mode)}
    audience = expected.get("default_exposure_audience")
    if audience:
        values["EXPOSURE_AUDIENCE"] = str(audience)
    return values


def auth_profile_summary(mode: str) -> str:
    expected = AUTH_MODE_EXPECTATIONS.get(mode, AUTH_MODE_EXPECTATIONS.get("custom", {}))
    return str(expected.get("scope", "운영자가 직접 관리하는 custom flag 조합"))


def verify_auth_profiles_yaml_consistency(project_root: Path) -> list[str]:
    """`configs/auth_profiles.yaml`의 구조적 완전성을 검증한다.

    Checks that each non-custom profile has all required semantic and bool fields.
    Returns a list of violation messages. Empty list means the YAML is structurally valid.

    After the switch to YAML-as-source-of-truth, this function is a semantic completeness
    validator rather than a drift check between two parallel representations.
    """
    yaml_path = project_root / "configs" / "auth_profiles.yaml"
    if not yaml_path.exists():
        return [f"configs/auth_profiles.yaml not found at {yaml_path}"]

    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"configs/auth_profiles.yaml YAML parse error: {exc}"]

    profiles = data.get("profiles", {}) if isinstance(data, dict) else {}
    violations: list[str] = []

    for mode, profile in profiles.items():
        if mode == "custom":
            continue
        if not isinstance(profile, dict):
            violations.append(f"auth_profiles.yaml[{mode}] is not a mapping")
            continue
        for field in _REQUIRED_FIELDS:
            if field not in profile:
                violations.append(f"auth_profiles.yaml[{mode}] missing required field: {field!r}")

    return violations


NON_LOCAL_ENVS = {"staging", "production", "prod"}
INTERNAL_TRUSTED_EVIDENCE_ENV = "INTERNAL_TRUSTED_AUTH_EVIDENCE"
CUSTOM_AUTH_RISK_ACCEPTED_ENV = "CUSTOM_AUTH_RISK_ACCEPTED"
CUSTOM_AUTH_RISK_TICKET_ENV = "CUSTOM_AUTH_RISK_TICKET"
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


def _exposure_mode_from_env() -> str:
    return _env("EXPOSURE_MODE", "master_open")


def _exposure_profile(project_root: Path, exposure_mode: str | None = None) -> dict[str, Any]:
    if exposure_mode is None:
        exposure_mode = _exposure_mode_from_env()
    data = _read_yaml(project_root / "configs" / "exposure_profiles.yaml")
    canonical_mode = exposure_mode
    profiles = data.get("profiles", {})
    return profiles.get(canonical_mode, profiles.get("private_network", {}))


def _exposure_services(project_root: Path) -> dict[str, Any]:
    data = _read_yaml(project_root / "configs" / "services.yaml")
    services = data.get("services", {})
    return services if isinstance(services, dict) else {}


def _compose_host_port_services(project_root: Path) -> list[str]:
    compose = _read_yaml(project_root / "ops" / "compose" / "full-stack.private-network.yaml")
    services = compose.get("services", {})
    published: list[str] = []
    if not isinstance(services, dict):
        return published
    for name, cfg in services.items():
        if isinstance(cfg, dict) and cfg.get("ports"):
            published.append(str(name))
    return published


def _exposure_host_published_services(project_root: Path, exposure_mode: str | None = None) -> list[str]:
    """지정한 exposure 프로필에서 host port를 공개하는 서비스 이름 목록을 반환한다."""
    profile = _exposure_profile(project_root, exposure_mode)
    return list(profile.get("host_published", []))


def auth_status_document(settings: AppSettings, project_root: Path, env_path: Path | None = None) -> dict[str, Any]:
    env_path = env_path or (project_root / ".env")
    published_services = _compose_host_port_services(project_root)
    exposure_mode = _exposure_mode_from_env()

    data = _read_yaml(project_root / "configs" / "exposure_profiles.yaml")
    canonical_mode = exposure_mode
    exposure_published = _exposure_host_published_services(project_root, canonical_mode)
    profile = _exposure_profile(project_root, canonical_mode)

    auth_owner = AUTH_MODE_EXPECTATIONS.get(settings.security.auth_mode, {}).get("auth_owner", "app")
    return {
        "env_file": {
            "path": str(env_path),
            "exists": env_path.exists(),
            "repository_default": env_path.resolve() == (project_root / ".env").resolve(),
        },
        "auth_mode": settings.security.auth_mode,
        "app_env": settings.app_env,
        "mode_scope": AUTH_MODE_EXPECTATIONS.get(settings.security.auth_mode, {}).get("scope", "unknown"),
        "auth_owner": auth_owner,
        "exposure_mode": exposure_mode,
        "canonical_exposure_mode": canonical_mode,
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
        "exposure": {
            "exposure_mode": exposure_mode,
            "canonical_mode": canonical_mode,
            "host_published_services": exposure_published,
            "diagnostics": profile.get("diagnostics", {}),
        },
    }


def diagnose_auth(settings: AppSettings, project_root: Path) -> list[AuthFinding]:
    findings: list[AuthFinding] = []
    mode = settings.security.auth_mode
    expected = AUTH_MODE_EXPECTATIONS.get(mode)
    if expected is None:
        findings.append(AuthFinding("WARN", "AUTH_MODE_UNKNOWN", f"AUTH_MODE={mode!r}는 알려진 profile이 아니므로 custom으로 해석합니다."))
        expected = AUTH_MODE_EXPECTATIONS.get("custom", {})

    if mode not in ("custom",):
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

    is_internal_trusted = mode == "internal_trusted"
    is_local_open_trusted_lan = (
        mode == "local_open"
        and _env("EXPOSURE_MODE", "") == "master_open"
        and _env("EXPOSURE_AUDIENCE", "") == "private_lan"
    )
    auth_owner = str(expected.get("auth_owner", "app"))

    if non_local and is_internal_trusted:
        evidence = _env(INTERNAL_TRUSTED_EVIDENCE_ENV, "").strip()
        if not evidence:
            findings.append(AuthFinding(
                "FAIL",
                "INTERNAL_TRUSTED_EVIDENCE_MISSING",
                f"AUTH_MODE=internal_trusted delegates app-level auth to {auth_owner}; "
                f"set {INTERNAL_TRUSTED_EVIDENCE_ENV} with network/edge/caller ownership evidence.",
            ))

    if non_local and mode == "custom":
        accepted = _env(CUSTOM_AUTH_RISK_ACCEPTED_ENV, "").lower() in ("1", "true")
        ticket = _env(CUSTOM_AUTH_RISK_TICKET_ENV, "").strip()
        if not accepted or not ticket:
            findings.append(AuthFinding(
                "FAIL",
                "CUSTOM_AUTH_RISK_ACCEPTANCE_REQUIRED",
                f"AUTH_MODE=custom in {settings.app_env} requires "
                f"{CUSTOM_AUTH_RISK_ACCEPTED_ENV}=true and {CUSTOM_AUTH_RISK_TICKET_ENV}.",
            ))

    if non_local and not settings.security.api_key_required:
        if is_internal_trusted or is_local_open_trusted_lan:
            findings.append(AuthFinding(
                "INFO",
                "AUTH_DELEGATED_TO_NETWORK",
                f"AUTH_MODE={mode}: API_KEY_REQUIRED=false — 인증 소유권이 "
                f"신뢰된 네트워크 경계에 위임됨 (APP_ENV={settings.app_env}).",
            ))
        else:
            findings.append(AuthFinding("FAIL", "PUBLIC_API_UNAUTHENTICATED_NON_LOCAL", f"APP_ENV={settings.app_env}인데 API_KEY_REQUIRED=false입니다."))

    if non_local and not settings.security.internal_service_auth_required:
        if is_internal_trusted or is_local_open_trusted_lan:
            findings.append(AuthFinding(
                "INFO",
                "INTERNAL_AUTH_DELEGATED",
                f"AUTH_MODE={mode}: INTERNAL_SERVICE_AUTH_REQUIRED=false — "
                f"내부 서비스 인증도 네트워크 소유권에 위임됨 (APP_ENV={settings.app_env}).",
            ))
        else:
            findings.append(AuthFinding("FAIL", "INTERNAL_SERVICE_AUTH_DISABLED_NON_LOCAL", f"APP_ENV={settings.app_env}인데 INTERNAL_SERVICE_AUTH_REQUIRED=false입니다."))

    if non_local and not settings.security.admin_api_key_required and not settings.security.admin_endpoints_internal_only:
        findings.append(AuthFinding("WARN", "ADMIN_ENDPOINTS_OPEN_NON_LOCAL", f"APP_ENV={settings.app_env}에서 ADMIN_API_KEY_REQUIRED=false 및 ADMIN_ENDPOINTS_INTERNAL_ONLY=false입니다."))
    if settings.security.admin_endpoints_internal_only and not settings.security.admin_api_key_required:
        findings.append(AuthFinding("WARN", "ADMIN_INTERNAL_ONLY_NOT_APP_ENFORCED", "ADMIN_ENDPOINTS_INTERNAL_ONLY=true는 배포/networking 선언이며 app-level CIDR enforcement는 아직 구현되지 않았습니다."))

    # Exposure-aware 진단 — 자유 텍스트가 아니라 구조화된 진단 필드로 판단한다.
    exposure_mode = _exposure_mode_from_env()
    data = _read_yaml(project_root / "configs" / "exposure_profiles.yaml")
    canonical_mode = exposure_mode
    exposure_profile_data = _exposure_profile(project_root, canonical_mode)
    diagnostics = exposure_profile_data.get("diagnostics", {})
    exposure_audience = _env("EXPOSURE_AUDIENCE", "").strip()

    if mode == "local_open" and (
        canonical_mode != "master_open" or exposure_audience != "private_lan"
    ):
        findings.append(
            AuthFinding(
                "FAIL",
                "LOCAL_OPEN_EXPOSURE_POLICY_MISMATCH",
                "AUTH_MODE=local_open requires EXPOSURE_MODE=master_open and "
                "EXPOSURE_AUDIENCE=private_lan so the trusted corporate network "
                "owns access control for Gateway, vLLM, and operations endpoints.",
            )
        )

    if data.get("canonical_modes") and canonical_mode not in data.get("profiles", {}):
        findings.append(AuthFinding(
            "FAIL",
            "EXPOSURE_MODE_UNKNOWN",
            f"EXPOSURE_MODE={exposure_mode!r} is not supported. Allowed canonical modes: {', '.join(data.get('canonical_modes', []))}.",
        ))

    if diagnostics.get("gateway_bypass_possible"):
        trusted_local_open = (
            mode == "local_open"
            and canonical_mode == "master_open"
            and exposure_audience == "private_lan"
        )
        findings.append(
            AuthFinding(
                "INFO" if trusted_local_open else "WARN",
                "EXPOSURE_GATEWAY_BYPASS_EXPECTED"
                if trusted_local_open
                else "EXPOSURE_GATEWAY_BYPASS_POSSIBLE",
                (
                    "AUTH_MODE=local_open + EXPOSURE_MODE=master_open/private_lan: "
                    "vLLM direct access is enabled by the trusted corporate-network policy."
                    if trusted_local_open
                    else (
                        f"EXPOSURE_MODE={canonical_mode}: "
                        "diagnostics.gateway_bypass_possible=true — vLLM runtime ports "
                        "are host-published. Gateway authentication bypass is possible."
                    )
                ),
            )
        )

    if diagnostics.get("direct_model_runtime_access"):
        findings.append(AuthFinding(
            "INFO",
            "EXPOSURE_DIRECT_MODEL_RUNTIME_ACCESS",
            f"EXPOSURE_MODE={canonical_mode}: vLLM runtimes are host-published (direct_model_runtime_access=true). "
            "This is the intended trusted-corporate-network property of this exposure mode.",
        ))

    if diagnostics.get("direct_operations_endpoints"):
        findings.append(AuthFinding(
            "INFO",
            "EXPOSURE_DIRECT_OPERATIONS_ENDPOINTS",
            f"EXPOSURE_MODE={canonical_mode}: Prometheus, DCGM, cAdvisor are host-published (direct_operations_endpoints=true). "
            "This is the intended trusted-corporate-network property of this exposure mode.",
        ))

    if diagnostics.get("requires_exposure_audience"):
        audience = exposure_audience
        allowed_audiences: list[str] = data.get("exposure_audience", {}).get("allowed_values", [])
        if not audience:
            allowed_str = "|".join(allowed_audiences) if allowed_audiences else "local_only|private_lan|vpn|public"
            findings.append(AuthFinding(
                "FAIL",
                "EXPOSURE_AUDIENCE_MISSING",
                f"EXPOSURE_MODE={canonical_mode} requires EXPOSURE_AUDIENCE to be set. "
                f"Allowed values: {allowed_str}. "
                "Set EXPOSURE_AUDIENCE to declare who can reach the host-published ports.",
            ))
        elif allowed_audiences and audience not in allowed_audiences:
            findings.append(AuthFinding(
                "FAIL",
                "EXPOSURE_AUDIENCE_INVALID_VALUE",
                f"EXPOSURE_AUDIENCE={audience!r} is not a valid value. "
                f"Allowed values: {', '.join(allowed_audiences)}.",
            ))
        else:
            if audience == "local_only":
                services_data = _exposure_services(project_root)
                published_svc_names: list[str] = exposure_profile_data.get("host_published", [])
                open_bind_svcs: list[str] = []
                for svc_name in published_svc_names:
                    svc = services_data.get(svc_name, {})
                    bind_env = svc.get("host_env_bind", "")
                    default_bind = svc.get("default_bind", "0.0.0.0")
                    actual_bind = _env(bind_env, default_bind) if bind_env else default_bind
                    if actual_bind == "0.0.0.0":
                        open_bind_svcs.append(f"{svc.get('compose_service', svc_name)} ({bind_env or 'default'}={actual_bind})")
                if open_bind_svcs:
                    preview = ", ".join(open_bind_svcs[:3])
                    if len(open_bind_svcs) > 3:
                        preview += f" ... ({len(open_bind_svcs)} total)"
                    findings.append(AuthFinding(
                        "FAIL",
                        "EXPOSURE_LOCAL_ONLY_BIND_MISMATCH",
                        f"EXPOSURE_AUDIENCE=local_only but services are bound to 0.0.0.0 (all interfaces): {preview}. "
                        "Set *_BIND_ADDR=127.0.0.1 for all host-published services, or change EXPOSURE_AUDIENCE.",
                    ))
            if audience == "public":
                if _env("ALLOW_PUBLIC_OPERATIONS_ENDPOINTS", "").lower() not in ("1", "true"):
                    findings.append(AuthFinding(
                        "FAIL",
                        "EXPOSURE_PUBLIC_AUDIENCE_WITHOUT_EXPLICIT_OPT_IN",
                        "EXPOSURE_AUDIENCE=public requires ALLOW_PUBLIC_OPERATIONS_ENDPOINTS=true as explicit opt-in. "
                        "Setting this on a public network exposes vLLM APIs and operations endpoints without Gateway auth.",
                    ))

    # Legacy compose 기반 체크 (private-network base compose에서는 여전히 유용)
    published = set(_compose_host_port_services(project_root))
    exposure_published = set(exposure_profile_data.get("host_published", []))
    if "risk-adapter" in published and "risk_adapter" not in exposure_published:
        findings.append(AuthFinding("WARN", "RISK_ADAPTER_HOST_PORT_PUBLISHED", "reference compose 파일에서 Risk Adapter host port가 published 상태입니다. private network 또는 firewall로 보호하세요."))
    for service in sorted(published & {"prometheus", "grafana", "cadvisor"}):
        if service not in exposure_published:
            findings.append(AuthFinding("WARN", "OBSERVABILITY_HOST_PORT_PUBLISHED", f"{service} host port가 reference compose 파일에서 published 상태입니다. shared 환경에서는 접근을 제한하세요."))

    if not findings:
        findings.append(AuthFinding("OK", "AUTH_POLICY_OK", "인증 제어 플레인에서 발견된 문제가 없습니다."))
    return findings


def render_auth_status(settings: AppSettings, project_root: Path, env_path: Path | None = None) -> str:
    doc = auth_status_document(settings, project_root, env_path)
    lines = [
        f"인증 profile: {doc['auth_mode']}",
        f"APP_ENV: {doc['app_env']}",
        f"인증 소유권: {doc['auth_owner']}",
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
    lines.extend(["", "Exposure"])
    exposure = doc["exposure"]
    canonical = exposure["canonical_mode"]
    lines.append(f"  EXPOSURE_MODE: {exposure['exposure_mode']}" + (f" → {canonical}" if canonical != exposure["exposure_mode"] else ""))
    lines.append(f"  Host-published: {', '.join(exposure['host_published_services']) if exposure['host_published_services'] else 'none'}")
    diag = exposure["diagnostics"]
    if any(diag.values()):
        lines.append("  Diagnostics:")
        for k, v in diag.items():
            if v:
                lines.append(f"    {k}: {v}")
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
