from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .auth_control import auth_profile_env_values


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AccessProfile:
    name: str
    description: str
    auth_mode: str
    exposure_mode: str
    exposure_audience: str
    host_bind_default: str
    host_bind_policy: str
    external_tls_owner: str


def _mapping(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return document


def access_profile_names(root: Path = _PROJECT_ROOT) -> tuple[str, ...]:
    profiles = _mapping(root / "configs" / "access_profiles.yaml").get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("configs/access_profiles.yaml must define profiles")
    return tuple(str(name) for name in profiles)


def default_access_profile(root: Path = _PROJECT_ROOT) -> str:
    document = _mapping(root / "configs" / "access_profiles.yaml")
    default = str(document.get("default_profile", ""))
    if default not in access_profile_names(root):
        raise ValueError("configs/access_profiles.yaml default_profile must reference a profile")
    return default


def load_access_profile(name: str, root: Path = _PROJECT_ROOT) -> AccessProfile:
    profiles = _mapping(root / "configs" / "access_profiles.yaml").get("profiles", {})
    raw = profiles.get(name) if isinstance(profiles, dict) else None
    if not isinstance(raw, dict):
        allowed = ", ".join(access_profile_names(root))
        raise ValueError(f"unknown access profile {name!r}; allowed: {allowed}")
    required = (
        "description",
        "auth_mode",
        "exposure_mode",
        "exposure_audience",
        "host_bind_default",
        "host_bind_policy",
        "external_tls_owner",
    )
    missing = [field for field in required if field not in raw]
    if missing:
        raise ValueError(f"access profile {name!r} is missing: {', '.join(missing)}")
    return AccessProfile(name=name, **{field: str(raw[field]) for field in required})


def access_profile_env_values(
    name: str,
    root: Path = _PROJECT_ROOT,
    *,
    current: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve a user-facing access intent into existing policy primitives."""
    profile = load_access_profile(name, root)
    values = {
        "ACCESS_PROFILE": profile.name,
        "EXPOSURE_MODE": profile.exposure_mode,
        "EXPOSURE_AUDIENCE": profile.exposure_audience,
    }
    values.update(auth_profile_env_values(profile.auth_mode))

    services = _mapping(root / "configs" / "services.yaml").get("services")
    if not isinstance(services, dict):
        raise ValueError("configs/services.yaml must define services")
    for service in services.values():
        if not isinstance(service, dict):
            continue
        bind_key = service.get("host_env_bind")
        if bind_key:
            key = str(bind_key)
            if profile.host_bind_policy == "operator" and current and current.get(key):
                values[key] = current[key]
            else:
                values[key] = profile.host_bind_default
    return values


def access_profile_changes(
    name: str,
    current: Mapping[str, str],
    root: Path = _PROJECT_ROOT,
) -> list[dict[str, str | bool]]:
    expected = access_profile_env_values(name, root, current=current)
    return [
        {
            "key": key,
            "before": current.get(key, "<unset>"),
            "after": value,
            "changed": current.get(key) != value,
        }
        for key, value in expected.items()
    ]


def access_profile_mismatches(
    name: str,
    current: Mapping[str, str],
    root: Path = _PROJECT_ROOT,
) -> list[str]:
    return [
        f"{change['key']}={change['before']!r}; expected {change['after']!r}"
        for change in access_profile_changes(name, current, root)
        if change["changed"]
    ]
