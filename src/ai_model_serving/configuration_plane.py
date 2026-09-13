"""Configuration Plane metadata and effective-value projection."""

from __future__ import annotations

from typing import Any

from .configuration import load_yaml_mapping
from .configuration_bindings import CONFIGURATION_BINDINGS, CONFIGURATION_PROJECTION_IDS
from .configuration_schema import (
    CONFIGURATION_SCHEMA_VERSION,
    validate_configuration_schema_document,
)
from .operator_configuration import (
    ConfigurationValueResolver,
    OperatorConfigurationState,
    repository_operator_defaults,
)
from .project_paths import resolve_project_root


def configuration_schema_items() -> list[dict[str, Any]]:
    document = load_yaml_mapping(resolve_project_root() / "configs" / "configuration_schema.yaml")
    try:
        return validate_configuration_schema_document(
            document,
            projection_ids=CONFIGURATION_PROJECTION_IDS,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def _public_schema_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "projection"}


def configuration_schema() -> dict[str, Any]:
    return {
        "version": CONFIGURATION_SCHEMA_VERSION,
        "items": [_public_schema_item(item) for item in configuration_schema_items()],
    }


def effective_configuration(
    settings: Any,
    resolver: ConfigurationValueResolver | None = None,
) -> dict[str, Any]:
    """Return metadata-aligned layered values without serializing secrets."""

    schema_items = configuration_schema_items()
    if resolver is None:
        resolver = ConfigurationValueResolver(
            repository_defaults=repository_operator_defaults(schema_items),
            operator_state=OperatorConfigurationState(revision=0, overrides={}),
        )

    items: list[dict[str, Any]] = []
    for schema in schema_items:
        binding = CONFIGURATION_BINDINGS.get(str(schema["projection"]))
        if binding is None:
            raise RuntimeError(f"unknown Configuration Plane projection: {schema['projection']!r}")
        secret = bool(schema["sensitive"])
        key = str(schema["key"])

        if schema["owner"] == "operator" and schema["control_surface"] == "configuration":
            layered = resolver.resolve(key)
            item = {
                "key": key,
                **layered,
                "owner": schema["owner"],
                "control_surface": schema["control_surface"],
                "editable": schema["editable"],
                "sensitive": False,
            }
        else:
            raw_value = binding.projection(settings)
            value = None if secret else (
                sorted(raw_value) if isinstance(raw_value, (set, frozenset)) else raw_value
            )
            item = {
                "key": key,
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
    return {
        "version": CONFIGURATION_SCHEMA_VERSION,
        "revision": resolver.revision,
        "items": items,
    }
