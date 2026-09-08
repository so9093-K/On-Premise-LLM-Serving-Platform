from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .configuration import load_yaml_mapping


NAMED_TOOL_CHOICE_UPSTREAM_REQUIRED_SINGLE = "required_single"
_NAMED_TOOL_CHOICE_UPSTREAM_MODES = frozenset({NAMED_TOOL_CHOICE_UPSTREAM_REQUIRED_SINGLE})


@dataclass(frozen=True)
class MainServingProfile:
    profile_id: str
    display_name: str
    served_model_name: str
    compatibility: dict[str, Any]
    deployed_input: tuple[str, ...]
    gateway_policy: dict[str, Any]


@dataclass(frozen=True)
class MainServingCatalog:
    public_model: str
    default_profile: str
    profiles: dict[str, MainServingProfile]


def load_main_serving_catalog(path: Path) -> MainServingCatalog:
    """Project only the API-serving contract from the runtime profile catalog.

    Docker image, command and GPU fields deliberately never enter this object. The
    Admin Sidecar continues to load the full runtime catalog from main_model.control.
    """
    document = load_yaml_mapping(path)
    public_model = str(document.get("public_model", ""))
    default_profile = str(document.get("default_profile", ""))
    raw_profiles = document.get("profiles")
    if not public_model or not default_profile or not isinstance(raw_profiles, dict):
        raise RuntimeError(f"main serving profile catalog is invalid: {path}")

    profiles: dict[str, MainServingProfile] = {}
    for profile_id, raw in raw_profiles.items():
        if not isinstance(raw, dict):
            raise RuntimeError(f"main serving profile {profile_id!r} must be a mapping")
        policy = raw.get("gateway_policy")
        if not isinstance(policy, dict):
            raise RuntimeError(f"main serving profile {profile_id!r} must declare gateway_policy")
        request_policy = policy.get("request_parameter_policy")
        if not isinstance(request_policy, dict):
            raise RuntimeError(
                f"main serving profile {profile_id!r} must declare "
                "gateway_policy.request_parameter_policy"
            )
        tool_policy = request_policy.get("tool_calling")
        if isinstance(tool_policy, dict) and "named_tool_choice_upstream" in tool_policy:
            mode = tool_policy["named_tool_choice_upstream"]
            if tool_policy.get("enabled") is not True or mode not in _NAMED_TOOL_CHOICE_UPSTREAM_MODES:
                raise RuntimeError(
                    f"main serving profile {profile_id!r} has invalid "
                    f"named_tool_choice_upstream: {mode!r}"
                )
        capabilities = raw.get("capabilities", {})
        deployed_input = capabilities.get("deployed_input", []) if isinstance(capabilities, dict) else []
        profiles[str(profile_id)] = MainServingProfile(
            profile_id=str(profile_id),
            display_name=str(raw.get("display_name", profile_id)),
            served_model_name=str(raw.get("served_model_name", public_model)),
            compatibility=dict(raw.get("compatibility", {})),
            deployed_input=tuple(str(item) for item in deployed_input),
            gateway_policy=dict(policy),
        )
    if default_profile not in profiles:
        raise RuntimeError(f"default main serving profile {default_profile!r} is not configured")
    if any(profile.served_model_name != public_model for profile in profiles.values()):
        raise RuntimeError("all main serving profiles must use the public_model served name")
    return MainServingCatalog(
        public_model=public_model,
        default_profile=default_profile,
        profiles=profiles,
    )
