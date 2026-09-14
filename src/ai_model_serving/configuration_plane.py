"""Configuration Plane metadata and effective-value projection."""

from __future__ import annotations

from typing import Any, Mapping

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


def _runtime_editable(item: Mapping[str, Any], write_status: Mapping[str, Any] | None) -> bool:
    declared = bool(item.get("editable"))
    if not declared:
        return False
    if item.get("owner") != "operator" or item.get("control_surface") != "configuration":
        return declared
    # ``editable``의 public 의미는 "이 인스턴스에서 지금 안전하게 수정 가능"이다.
    # metadata의 declared capability만으로 true를 내보내면 persistent state가 없거나
    # partial failure 상태인 인스턴스에서도 Console이 write action을 열게 된다.
    return bool(write_status and write_status.get("available") is True)


def _public_schema_item(
    item: dict[str, Any],
    write_status: Mapping[str, Any] | None,
) -> dict[str, Any]:
    public = {key: value for key, value in item.items() if key != "projection"}
    public["editable"] = _runtime_editable(item, write_status)
    return public


def configuration_schema(
    *,
    write_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": CONFIGURATION_SCHEMA_VERSION,
        "items": [
            _public_schema_item(item, write_status)
            for item in configuration_schema_items()
        ],
    }
    if write_status is not None:
        result["write_status"] = dict(write_status)
    return result


def effective_configuration(
    settings: Any,
    resolver: ConfigurationValueResolver | None = None,
    *,
    write_status: Mapping[str, Any] | None = None,
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
                "editable": _runtime_editable(schema, write_status),
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
                "editable": _runtime_editable(schema, write_status),
                "sensitive": secret,
            }
            if secret:
                item["configured"] = bool(raw_value)
        items.append(item)
    result: dict[str, Any] = {
        "version": CONFIGURATION_SCHEMA_VERSION,
        "revision": resolver.revision,
        "items": items,
    }
    if write_status is not None:
        result["write_status"] = dict(write_status)
    return result
