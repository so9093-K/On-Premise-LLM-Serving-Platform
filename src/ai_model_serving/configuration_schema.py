from __future__ import annotations

from typing import Any, Collection


CONFIGURATION_SCHEMA_VERSION = 2
CONFIGURATION_OWNERS = frozenset({"repository", "operator", "deployment", "runtime", "secret"})
CONFIGURATION_VALUE_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "enum", "url", "array", "secret"}
)
CONFIGURATION_APPLY_MODES = frozenset(
    {"hot_reload", "service_restart", "runtime_restart", "compose_restart", "redeploy"}
)
CONFIGURATION_CONTROL_SURFACES = frozenset(
    {"configuration", "deployment", "runtime", "main_model", "secret", "access_profile", "repository"}
)
CONFIGURATION_RISKS = frozenset({"info", "low", "medium", "high", "critical"})

_REQUIRED_FIELDS = frozenset(
    {
        "key",
        "projection",
        "label",
        "group",
        "order",
        "type",
        "owner",
        "editable",
        "control_surface",
        "sensitive",
        "effective_source",
        "apply_mode",
        "risk",
        "applicability",
        "meaning",
        "help",
        "related_adrs",
    }
)
_NUMERIC_TYPES = frozenset({"integer", "number"})


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _numeric_constraint(item: dict[str, Any], name: str, *, index: int) -> int | float | None:
    if name not in item:
        return None
    value = item[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"configuration_schema.yaml items[{index}].{name} must be numeric")
    if item["type"] == "integer" and not isinstance(value, int):
        raise ValueError(
            f"configuration_schema.yaml items[{index}].{name} must be an integer for integer type"
        )
    return value


def validate_configuration_schema_document(
    document: dict[str, Any],
    *,
    projection_ids: Collection[str],
) -> list[dict[str, Any]]:
    """Validate the Configuration Plane metadata grammar used by runtime and CI.

    Repository-specific cross-contract checks belong in governance validation; this
    function owns only the schema document's intrinsic grammar and ownership rules.
    """

    items = document.get("items")
    if document.get("version") != CONFIGURATION_SCHEMA_VERSION or not isinstance(items, list) or not items:
        raise ValueError(
            f"configuration_schema.yaml must declare version {CONFIGURATION_SCHEMA_VERSION} and non-empty items"
        )

    keys: set[str] = set()
    projections: set[str] = set()
    group_orders: set[tuple[str, int]] = set()
    normalized: list[dict[str, Any]] = []

    for index, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            raise ValueError(f"configuration_schema.yaml items[{index}] must be a mapping")
        item = dict(raw_item)
        missing = _REQUIRED_FIELDS - set(item)
        if missing:
            raise ValueError(
                f"configuration_schema.yaml items[{index}] missing: {', '.join(sorted(missing))}"
            )

        key = item["key"]
        projection = item["projection"]
        if not _non_empty_string(key) or key in keys:
            raise ValueError(
                f"configuration_schema.yaml items[{index}].key must be unique and non-empty"
            )
        if not _non_empty_string(projection) or projection in projections:
            raise ValueError(
                f"configuration_schema.yaml items[{index}].projection must be unique and non-empty"
            )
        if projection not in projection_ids:
            raise ValueError(
                f"configuration_schema.yaml items[{index}].projection is not allowlisted"
            )

        for field in ("label", "group", "meaning", "help"):
            if not _non_empty_string(item[field]):
                raise ValueError(
                    f"configuration_schema.yaml items[{index}].{field} must be non-empty"
                )

        order = item["order"]
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            raise ValueError(
                f"configuration_schema.yaml items[{index}].order must be a non-negative integer"
            )
        group_order = (item["group"], order)
        if group_order in group_orders:
            raise ValueError(
                f"configuration_schema.yaml items[{index}] duplicates group/order {group_order!r}"
            )

        value_type = item["type"]
        if value_type not in CONFIGURATION_VALUE_TYPES:
            raise ValueError(f"configuration_schema.yaml items[{index}].type is invalid")
        if item["owner"] not in CONFIGURATION_OWNERS or item["effective_source"] not in CONFIGURATION_OWNERS:
            raise ValueError(
                f"configuration_schema.yaml items[{index}] has invalid owner or effective_source"
            )
        if item["apply_mode"] not in CONFIGURATION_APPLY_MODES:
            raise ValueError(f"configuration_schema.yaml items[{index}].apply_mode is invalid")
        if item["control_surface"] not in CONFIGURATION_CONTROL_SURFACES:
            raise ValueError(f"configuration_schema.yaml items[{index}].control_surface is invalid")
        if item["risk"] not in CONFIGURATION_RISKS:
            raise ValueError(f"configuration_schema.yaml items[{index}].risk is invalid")
        if not isinstance(item["editable"], bool) or not isinstance(item["sensitive"], bool):
            raise ValueError(
                f"configuration_schema.yaml items[{index}] editable and sensitive must be boolean"
            )

        if (value_type == "secret") != item["sensitive"]:
            raise ValueError(
                f"configuration_schema.yaml items[{index}] secret type and sensitive flag must agree"
            )
        if item["owner"] == "secret" and item["effective_source"] != "secret":
            raise ValueError(
                f"configuration_schema.yaml items[{index}] secret owner must use secret source"
            )
        if item["editable"] and (
            item["owner"] != "operator" or item["control_surface"] != "configuration"
        ):
            raise ValueError(
                f"configuration_schema.yaml items[{index}] editable values must be operator-owned configuration controls"
            )
        if item["control_surface"] == "configuration" and item["owner"] != "operator":
            raise ValueError(
                f"configuration_schema.yaml items[{index}] configuration controls must be operator-owned"
            )

        applicability = item["applicability"]
        if not isinstance(applicability, dict) or set(applicability) != {"features"}:
            raise ValueError(
                f"configuration_schema.yaml items[{index}].applicability must contain only features"
            )
        features = applicability["features"]
        if not isinstance(features, list) or not all(_non_empty_string(feature) for feature in features):
            raise ValueError(
                f"configuration_schema.yaml items[{index}].applicability.features must be a string list"
            )
        if len(features) != len(set(features)):
            raise ValueError(
                f"configuration_schema.yaml items[{index}].applicability.features must be unique"
            )

        related_adrs = item["related_adrs"]
        if not isinstance(related_adrs, list) or not related_adrs or not all(
            isinstance(adr, str) and adr.startswith("ADR-") for adr in related_adrs
        ):
            raise ValueError(
                f"configuration_schema.yaml items[{index}].related_adrs must be a non-empty ADR id list"
            )

        minimum = _numeric_constraint(item, "minimum", index=index)
        maximum = _numeric_constraint(item, "maximum", index=index)
        if (minimum is not None or maximum is not None) and value_type not in _NUMERIC_TYPES:
            raise ValueError(
                f"configuration_schema.yaml items[{index}] numeric constraints require integer or number type"
            )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(
                f"configuration_schema.yaml items[{index}] minimum cannot exceed maximum"
            )

        unit = item.get("unit")
        if unit is not None:
            if value_type not in _NUMERIC_TYPES or not _non_empty_string(unit):
                raise ValueError(
                    f"configuration_schema.yaml items[{index}].unit requires a numeric type and non-empty string"
                )

        enum_values = item.get("enum")
        if value_type == "enum":
            if not isinstance(enum_values, list) or not enum_values:
                raise ValueError(
                    f"configuration_schema.yaml items[{index}].enum must be a non-empty list for enum type"
                )
            if any(isinstance(value, (dict, list, set)) for value in enum_values):
                raise ValueError(
                    f"configuration_schema.yaml items[{index}].enum values must be scalar"
                )
            if len({repr(value) for value in enum_values}) != len(enum_values):
                raise ValueError(
                    f"configuration_schema.yaml items[{index}].enum values must be unique"
                )
        elif "enum" in item:
            raise ValueError(
                f"configuration_schema.yaml items[{index}].enum is only valid for enum type"
            )

        keys.add(key)
        projections.add(projection)
        group_orders.add(group_order)
        normalized.append(item)

    return normalized
