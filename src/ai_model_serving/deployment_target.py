from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .configuration import load_yaml_mapping


KNOWN_FEATURES = frozenset(
    {
        "chat",
        "embeddings",
        "retrieval",
        "risk",
        "runtime_control",
        "model_switching",
        "gpu_admission",
    }
)
_CONTROL_MODES = frozenset({"sidecar", "static"})
_LIFECYCLE_OWNERS = frozenset({"platform", "external"})
_VALIDATION_STATUSES = frozenset({"verified", "implemented", "planned", "unvalidated"})


@dataclass(frozen=True)
class DeploymentTarget:
    target_id: str
    display_name: str
    platform: str
    runtime_backend: str
    control_mode: str
    lifecycle_owner: str
    internal_service_token_required: bool
    validation_status: str
    features: frozenset[str]

    def supports(self, feature: str) -> bool:
        return feature in self.features

    @property
    def controllable(self) -> bool:
        return self.control_mode == "sidecar" and self.lifecycle_owner == "platform"


def load_deployment_target(path: Path, target_id: str | None = None) -> DeploymentTarget:
    document = load_yaml_mapping(path)
    selected = target_id or str(document.get("default_target", ""))
    targets = document.get("targets")
    if not isinstance(targets, dict) or not selected:
        raise RuntimeError(f"deployment target catalog is invalid: {path}")
    raw = targets.get(selected)
    if not isinstance(raw, dict):
        allowed = ", ".join(sorted(str(item) for item in targets))
        raise RuntimeError(f"unknown DEPLOYMENT_TARGET {selected!r}; allowed: {allowed}")

    control_mode = str(raw.get("control_mode", ""))
    lifecycle_owner = str(raw.get("lifecycle_owner", ""))
    internal_service_token_required = raw.get("internal_service_token_required")
    validation_status = str(raw.get("validation_status", ""))
    raw_features: Any = raw.get("features")
    if control_mode not in _CONTROL_MODES:
        raise RuntimeError(f"deployment target {selected!r} has invalid control_mode {control_mode!r}")
    if lifecycle_owner not in _LIFECYCLE_OWNERS:
        raise RuntimeError(
            f"deployment target {selected!r} has invalid lifecycle_owner {lifecycle_owner!r}"
        )
    if not isinstance(internal_service_token_required, bool):
        raise RuntimeError(
            f"deployment target {selected!r} internal_service_token_required must be boolean"
        )
    if validation_status not in _VALIDATION_STATUSES:
        raise RuntimeError(
            f"deployment target {selected!r} has invalid validation_status {validation_status!r}"
        )
    if not isinstance(raw_features, dict):
        raise RuntimeError(f"deployment target {selected!r} features must be a mapping")
    unknown_features = set(raw_features) - KNOWN_FEATURES
    if unknown_features:
        raise RuntimeError(
            f"deployment target {selected!r} has unknown features: {', '.join(sorted(unknown_features))}"
        )
    non_boolean_features = [
        str(key) for key, enabled in raw_features.items() if not isinstance(enabled, bool)
    ]
    if non_boolean_features:
        raise RuntimeError(
            f"deployment target {selected!r} has non-boolean features: "
            f"{', '.join(sorted(non_boolean_features))}"
        )
    platform = str(raw.get("platform", "")).strip()
    runtime_backend = str(raw.get("runtime_backend", "")).strip()
    if not platform or not runtime_backend:
        raise RuntimeError(
            f"deployment target {selected!r} requires platform and runtime_backend"
        )
    expected_owner = "platform" if control_mode == "sidecar" else "external"
    if lifecycle_owner != expected_owner:
        raise RuntimeError(
            f"deployment target {selected!r} control_mode={control_mode!r} requires "
            f"lifecycle_owner={expected_owner!r}"
        )
    features = frozenset(str(key) for key, enabled in raw_features.items() if enabled is True)
    if "chat" not in features:
        raise RuntimeError(f"deployment target {selected!r} must enable chat")
    if "retrieval" in features and "embeddings" not in features:
        raise RuntimeError(f"deployment target {selected!r}: retrieval requires embeddings")
    lifecycle_flags = [
        feature in features
        for feature in ("runtime_control", "model_switching", "gpu_admission")
    ]
    if len(set(lifecycle_flags)) != 1:
        raise RuntimeError(
            f"deployment target {selected!r} must enable or disable runtime_control, "
            "model_switching, and gpu_admission together"
        )
    if control_mode == "static" and ({"runtime_control", "model_switching", "gpu_admission"} & features):
        raise RuntimeError(f"static deployment target {selected!r} cannot enable lifecycle control features")

    return DeploymentTarget(
        target_id=selected,
        display_name=str(raw.get("display_name", selected)),
        platform=platform,
        runtime_backend=runtime_backend,
        control_mode=control_mode,
        lifecycle_owner=lifecycle_owner,
        internal_service_token_required=internal_service_token_required,
        validation_status=validation_status,
        features=features,
    )
