"""Read-only Configuration Plane projection.

The projection deliberately reads resolved settings (including the app-scoped
runtime configuration view) rather than raw environment files. It never
serializes secret values; a future mutable operator store will be added behind
this boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .configuration import load_yaml_mapping
from .configuration_schema import (
    CONFIGURATION_SCHEMA_VERSION,
    validate_configuration_schema_document,
)
from .project_paths import resolve_project_root

ConfigurationProjection = Callable[[Any], Any]

# YAML은 어떤 값을 설명할지 소유하지만, resolved settings의 어느 attribute까지 외부 API가
# 읽을 수 있는지는 코드의 명시적인 allowlist가 소유한다. 이 경계가 없으면 YAML의
# 단순 오기(`security.admin_api_keys` 같은 경로)가 secret export로 바뀔 수 있다.
_PROJECTIONS: dict[str, ConfigurationProjection] = {
    "deployment_target": lambda settings: settings.deployment_target.target_id,
    "deployment_control_mode": lambda settings: settings.deployment_target.control_mode,
    "deployment_lifecycle_owner": lambda settings: settings.deployment_target.lifecycle_owner,
    "deployment_features": lambda settings: settings.deployment_target.features,
    "security_auth_mode": lambda settings: settings.security.auth_mode,
    "security_api_keys": lambda settings: settings.security.api_keys,
    "main_llm_base_url": lambda settings: settings.runtime("main_llm").base_url,
    "required_runtime_keys": lambda settings: settings.required_runtime_keys,
    "max_retrieval_documents": lambda settings: settings.max_retrieval_documents,
    "streaming_max_duration_seconds": lambda settings: settings.streaming_max_duration_seconds,
    "streaming_max_chunks": lambda settings: settings.streaming_max_chunks,
    "streaming_max_bytes": lambda settings: settings.streaming_max_bytes,
}
CONFIGURATION_PROJECTION_IDS = frozenset(_PROJECTIONS)


def _schema_items() -> list[dict[str, Any]]:
    document = load_yaml_mapping(resolve_project_root() / "configs" / "configuration_schema.yaml")
    try:
        return validate_configuration_schema_document(
            document,
            projection_ids=CONFIGURATION_PROJECTION_IDS,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def _public_schema_item(item: dict[str, Any]) -> dict[str, Any]:
    """Keep the internal projection identifier out of the public API contract."""
    return {key: value for key, value in item.items() if key != "projection"}


def configuration_schema() -> dict[str, Any]:
    return {
        "version": CONFIGURATION_SCHEMA_VERSION,
        "items": [_public_schema_item(item) for item in _schema_items()],
    }


def effective_configuration(settings: Any) -> dict[str, Any]:
    """Return metadata-aligned effective values without serializing secrets."""
    items: list[dict[str, Any]] = []
    for schema in _schema_items():
        projection = _PROJECTIONS.get(str(schema["projection"]))
        if projection is None:
            raise RuntimeError(f"unknown Configuration Plane projection: {schema['projection']!r}")
        raw_value = projection(settings)
        secret = bool(schema["sensitive"])
        value = None if secret else (sorted(raw_value) if isinstance(raw_value, (set, frozenset)) else raw_value)
        item = {
            "key": schema["key"],
            "effective_value": value,
            "effective_source": schema["effective_source"],
            "owner": schema["owner"],
            "control_surface": schema["control_surface"],
            "editable": schema["editable"],
            "sensitive": secret,
        }
        if secret:
            item["configured"] = bool(raw_value)
        items.append(item)
    return {"version": CONFIGURATION_SCHEMA_VERSION, "items": items}
