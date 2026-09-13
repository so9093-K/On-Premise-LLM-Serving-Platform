from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


ConfigurationProjection = Callable[[Any], Any]


@dataclass(frozen=True)
class ConfigurationBinding:
    """Code-owned exposure/source binding for one schema projection id.

    YAML owns presentation and policy metadata. This registry owns the executable
    boundary: how a projection reads resolved settings and, for operator-controlled
    runtime values, where its repository default comes from and which runtime
    snapshot field receives the effective value.
    """

    projection: ConfigurationProjection
    repository_path: tuple[str, ...] | None = None
    runtime_field: str | None = None


CONFIGURATION_BINDINGS: dict[str, ConfigurationBinding] = {
    "deployment_target": ConfigurationBinding(
        projection=lambda settings: settings.deployment_target.target_id,
    ),
    "deployment_control_mode": ConfigurationBinding(
        projection=lambda settings: settings.deployment_target.control_mode,
    ),
    "deployment_lifecycle_owner": ConfigurationBinding(
        projection=lambda settings: settings.deployment_target.lifecycle_owner,
    ),
    "deployment_features": ConfigurationBinding(
        projection=lambda settings: settings.deployment_target.features,
    ),
    "security_auth_mode": ConfigurationBinding(
        projection=lambda settings: settings.security.auth_mode,
    ),
    "security_api_keys": ConfigurationBinding(
        projection=lambda settings: settings.security.api_keys,
    ),
    "main_llm_base_url": ConfigurationBinding(
        projection=lambda settings: settings.runtime("main_llm").base_url,
    ),
    "required_runtime_keys": ConfigurationBinding(
        projection=lambda settings: settings.required_runtime_keys,
    ),
    "max_retrieval_documents": ConfigurationBinding(
        projection=lambda settings: settings.max_retrieval_documents,
        repository_path=("operational_limits", "max_retrieval_documents"),
        runtime_field="max_retrieval_documents",
    ),
    "streaming_max_duration_seconds": ConfigurationBinding(
        projection=lambda settings: settings.streaming_max_duration_seconds,
        repository_path=("streaming", "max_duration_seconds"),
        runtime_field="streaming_max_duration_seconds",
    ),
    "streaming_max_chunks": ConfigurationBinding(
        projection=lambda settings: settings.streaming_max_chunks,
        repository_path=("streaming", "max_chunks"),
        runtime_field="streaming_max_chunks",
    ),
    "streaming_max_bytes": ConfigurationBinding(
        projection=lambda settings: settings.streaming_max_bytes,
        repository_path=("streaming", "max_bytes"),
        runtime_field="streaming_max_bytes",
    ),
}

CONFIGURATION_PROJECTION_IDS = frozenset(CONFIGURATION_BINDINGS)


def binding_for_projection(projection_id: str) -> ConfigurationBinding:
    try:
        return CONFIGURATION_BINDINGS[projection_id]
    except KeyError as exc:
        raise ValueError(f"unknown Configuration Plane projection: {projection_id!r}") from exc


def operator_runtime_bindings(
    schema_items: list[dict[str, Any]],
) -> dict[str, ConfigurationBinding]:
    """Derive operator runtime bindings from validated schema metadata.

    The schema decides which keys are operator-owned configuration controls. The
    code registry decides how those projection ids map to repository/runtime state.
    No second key list is maintained here.
    """

    bindings: dict[str, ConfigurationBinding] = {}
    for item in schema_items:
        if item.get("owner") != "operator" or item.get("control_surface") != "configuration":
            continue
        key = str(item["key"])
        binding = binding_for_projection(str(item["projection"]))
        if binding.repository_path is None or binding.runtime_field is None:
            raise ValueError(
                f"operator configuration projection lacks runtime binding: {item['projection']!r}"
            )
        bindings[key] = binding
    return bindings
