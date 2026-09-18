from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .access_profile import AccessProfile, load_access_profile
from .configuration import load_yaml_mapping
from .configuration_schema import CONFIGURATION_SCHEMA_VERSION
from .deployment_target import effective_published_compose_services
from .project_paths import resolve_project_root
from .settings_parts.env import env as _env
from .settings_parts.types import AppSettings


BOOTSTRAP_VERSION = 2
_LEGACY_PROFILE = "legacy/custom"
_LEGACY_PROFILE_DESCRIPTION = (
    "Access Profile이 선택되지 않은 advanced/custom 구성입니다. 저수준 인증·노출 설정을 진단용으로 사용합니다."
)


@dataclass(frozen=True)
class ControlPlaneBootstrapProjection:
    """Startup-resolved, browser-safe Control Plane discovery inputs.

    Repository YAML and process environment are composition inputs, not request-time
    dependencies. The Gateway resolves them once while the application is built; the
    public Bootstrap request only adds current Configuration Plane state and the
    request hostname needed for a local direct Grafana link.
    """

    platform_version: str
    release_id: str | None
    deployment_target: str
    deployment_display_name: str
    deployment_platform: str
    runtime_backend: str
    validation_status: str
    implementation_status: str
    qualification_status: str
    control_mode: str
    lifecycle_owner: str
    features: tuple[str, ...]
    access_profile: str
    access_description: str
    admin_auth_required: bool
    monitoring_available: bool
    grafana_available: bool
    grafana_direct_port: int | None
    docs_url: str | None
    redoc_url: str | None
    openapi_url: str | None


def _current_access_profile(root: Path) -> AccessProfile | None:
    """Return only an explicitly selected managed Access Profile."""
    name = _env("ACCESS_PROFILE", "").strip()
    if not name:
        return None
    try:
        return load_access_profile(name, root)
    except ValueError:
        # Do not reverse-match low-level auth/exposure flags into a friendly profile.
        # Unknown or absent profile selection remains explicit legacy/custom posture.
        return None


def _published_services(settings: AppSettings, root: Path, profile: AccessProfile | None) -> set[str]:
    target = settings.deployment_target
    static_services = effective_published_compose_services(target, root)
    if static_services is not None:
        return static_services

    # Dynamic targets consume exposure_profiles.yaml at deployment time. Managed
    # Access Profile is the supported composition SoT. Advanced/custom deployments
    # may name EXPOSURE_MODE directly; unknown modes fail closed to no public link.
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


def _grafana_projection(
    *,
    settings: AppSettings,
    root: Path,
    profile: AccessProfile | None,
) -> tuple[bool, int | None]:
    if not settings.deployment_target.runs_monitoring_stack:
        return False, None
    if "grafana" not in _published_services(settings, root, profile):
        return False, None

    # Monitoring may exist without a browser-safe direct URL. private/edge profiles
    # can put TLS/proxy ownership outside the Gateway, so only a managed profile with
    # no external TLS owner receives a direct host-port projection.
    if profile is None or profile.external_tls_owner != "none":
        return True, None
    return True, _service_host_port(root, "grafana")


def build_control_plane_bootstrap_projection(
    settings: AppSettings,
    *,
    root: Path | None = None,
) -> ControlPlaneBootstrapProjection:
    """Resolve repository/environment posture once at Gateway composition time."""
    project_root = resolve_project_root(root)
    profile = _current_access_profile(project_root)
    grafana_available, grafana_direct_port = _grafana_projection(
        settings=settings,
        root=project_root,
        profile=profile,
    )
    docs_enabled = settings.documentation.enabled
    target = settings.deployment_target
    return ControlPlaneBootstrapProjection(
        platform_version=settings.project_version,
        release_id=settings.deploy_release_id or None,
        deployment_target=target.target_id,
        deployment_display_name=target.display_name,
        deployment_platform=target.platform,
        runtime_backend=target.runtime_backend,
        validation_status=target.validation_status,
        implementation_status=target.implementation_status,
        qualification_status=target.qualification_status,
        control_mode=target.control_mode,
        lifecycle_owner=target.lifecycle_owner,
        features=tuple(sorted(target.features)),
        access_profile=profile.name if profile is not None else _LEGACY_PROFILE,
        access_description=(
            profile.description if profile is not None else _LEGACY_PROFILE_DESCRIPTION
        ),
        admin_auth_required=settings.security.admin_api_key_required,
        monitoring_available=target.runs_monitoring_stack,
        grafana_available=grafana_available,
        grafana_direct_port=grafana_direct_port,
        docs_url=settings.documentation.docs_url if docs_enabled else None,
        redoc_url=settings.documentation.redoc_url if docs_enabled else None,
        openapi_url=settings.documentation.openapi_url if docs_enabled else None,
    )


def control_plane_bootstrap_document(
    projection: ControlPlaneBootstrapProjection,
    *,
    configuration_revision: int,
    configuration_write_available: bool,
    request_hostname: str | None,
) -> dict[str, Any]:
    """Render the browser-safe discovery document without repository I/O."""
    grafana_href = None
    if projection.grafana_direct_port is not None and request_hostname:
        host = f"[{request_hostname}]" if ":" in request_hostname else request_hostname
        grafana_href = f"http://{host}:{projection.grafana_direct_port}/"

    return {
        "bootstrap_version": BOOTSTRAP_VERSION,
        "platform": {
            "version": projection.platform_version,
            "release_id": projection.release_id,
        },
        "deployment": {
            "target": projection.deployment_target,
            "display_name": projection.deployment_display_name,
            "platform": projection.deployment_platform,
            "runtime_backend": projection.runtime_backend,
            "validation_status": projection.validation_status,
            "implementation_status": projection.implementation_status,
            "qualification_status": projection.qualification_status,
            "control_mode": projection.control_mode,
            "lifecycle_owner": projection.lifecycle_owner,
            "features": list(projection.features),
        },
        "access": {
            "profile": projection.access_profile,
            "description": projection.access_description,
            "admin_auth_required": projection.admin_auth_required,
        },
        "configuration": {
            "schema_version": CONFIGURATION_SCHEMA_VERSION,
            "revision": configuration_revision,
            "write_available": configuration_write_available,
        },
        "monitoring": {
            "available": projection.monitoring_available,
            "grafana_available": projection.grafana_available,
        },
        "links": {
            "docs": projection.docs_url,
            "redoc": projection.redoc_url,
            "openapi": projection.openapi_url,
            "grafana": grafana_href,
        },
    }
