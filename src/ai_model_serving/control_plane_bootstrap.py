from __future__ import annotations

from pathlib import Path
from typing import Any

from .access_profile import AccessProfile, load_access_profile
from .configuration import load_yaml_mapping
from .configuration_schema import CONFIGURATION_SCHEMA_VERSION
from .deployment_target import effective_published_compose_services
from .project_paths import resolve_project_root
from .settings_parts.env import env as _env
from .settings_parts.types import AppSettings


BOOTSTRAP_VERSION = 1
_LEGACY_PROFILE = "legacy/custom"
_LEGACY_PROFILE_DESCRIPTION = (
    "Access Profile이 선택되지 않은 advanced/custom 구성입니다. 저수준 인증·노출 설정을 진단용으로 사용합니다."
)


def _current_access_profile(root: Path) -> AccessProfile | None:
    """Return only an explicitly selected managed Access Profile.

    Bootstrap must not infer a friendly profile name by reverse-matching AUTH_MODE or
    bind flags.  When ACCESS_PROFILE is absent or unknown, the browser sees the
    explicit legacy/custom posture instead of a guessed managed profile.
    """
    name = _env("ACCESS_PROFILE", "").strip()
    if not name:
        return None
    try:
        return load_access_profile(name, root)
    except ValueError:
        return None


def _published_services(settings: AppSettings, root: Path, profile: AccessProfile | None) -> set[str]:
    target = settings.deployment_target
    static_services = effective_published_compose_services(target, root)
    if static_services is not None:
        return static_services

    # Dynamic targets consume exposure_profiles.yaml at deployment time.  Managed
    # Access Profile은 그 exposure mode의 SoT이므로 동일 값을 사용한다. Advanced
    # configuration에서는 명시된 EXPOSURE_MODE만 신뢰하고, 모르는 값은 공개 서비스가
    # 없다고 처리해 link를 fail-closed 한다.
    exposure_mode = profile.exposure_mode if profile is not None else _env("EXPOSURE_MODE", "").strip()
    document = load_yaml_mapping(root / "configs" / "exposure_profiles.yaml")
    profiles = document.get("profiles")
    raw = profiles.get(exposure_mode) if isinstance(profiles, dict) else None
    if not isinstance(raw, dict):
        return set()
    published = raw.get("host_published")
    if not isinstance(published, list):
        return set()
    return {str(item) for item in published}


def _service_host_port(root: Path, service_id: str) -> int | None:
    services = load_yaml_mapping(root / "configs" / "services.yaml").get("services")
    service = services.get(service_id) if isinstance(services, dict) else None
    if not isinstance(service, dict):
        return None
    env_key = service.get("host_env_port")
    default = service.get("default_host_port")
    if not isinstance(env_key, str) or isinstance(default, bool) or not isinstance(default, int):
        return None
    raw = _env(env_key, str(default)).strip()
    if not raw.isascii() or not raw.isdecimal():
        return None
    port = int(raw)
    return port if 1 <= port <= 65535 else None


def _grafana_href(
    *,
    settings: AppSettings,
    root: Path,
    profile: AccessProfile | None,
    request_hostname: str | None,
) -> tuple[bool, str | None]:
    if not settings.deployment_target.runs_monitoring_stack:
        return False, None
    if "grafana" not in _published_services(settings, root, profile):
        return False, None

    # Monitoring may exist without a browser-safe direct URL. private/edge profiles
    # can put TLS/proxy ownership outside the Gateway, so inventing a scheme/host here
    # would make the Console point at a URL that may not exist. A direct link is only
    # guaranteed for the local profile, where the profile explicitly declares no
    # external TLS owner and Grafana is host-published on the same machine.
    if profile is None or profile.external_tls_owner != "none" or not request_hostname:
        return True, None
    port = _service_host_port(root, "grafana")
    if port is None:
        return True, None
    host = f"[{request_hostname}]" if ":" in request_hostname else request_hostname
    return True, f"http://{host}:{port}/"


def control_plane_bootstrap_document(
    settings: AppSettings,
    *,
    configuration_revision: int,
    configuration_write_available: bool,
    request_hostname: str | None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Project existing control-plane SoTs into the browser-safe discovery contract."""
    project_root = resolve_project_root(root)
    profile = _current_access_profile(project_root)
    grafana_available, grafana_href = _grafana_href(
        settings=settings,
        root=project_root,
        profile=profile,
        request_hostname=request_hostname,
    )
    docs_enabled = settings.documentation.enabled
    target = settings.deployment_target

    return {
        "bootstrap_version": BOOTSTRAP_VERSION,
        "platform": {
            "version": settings.project_version,
            "release_id": settings.deploy_release_id or None,
        },
        "deployment": {
            "target": target.target_id,
            "display_name": target.display_name,
            "platform": target.platform,
            "runtime_backend": target.runtime_backend,
            "validation_status": target.validation_status,
            "control_mode": target.control_mode,
            "lifecycle_owner": target.lifecycle_owner,
            "features": sorted(target.features),
        },
        "access": {
            "profile": profile.name if profile is not None else _LEGACY_PROFILE,
            "description": profile.description if profile is not None else _LEGACY_PROFILE_DESCRIPTION,
            "admin_auth_required": settings.security.admin_api_key_required,
        },
        "configuration": {
            "schema_version": CONFIGURATION_SCHEMA_VERSION,
            "revision": configuration_revision,
            "write_available": configuration_write_available,
        },
        "monitoring": {
            "available": target.runs_monitoring_stack,
            "grafana_available": grafana_available,
        },
        "links": {
            "docs": settings.documentation.docs_url if docs_enabled else None,
            "redoc": settings.documentation.redoc_url if docs_enabled else None,
            "openapi": settings.documentation.openapi_url if docs_enabled else None,
            "grafana": grafana_href,
        },
    }
