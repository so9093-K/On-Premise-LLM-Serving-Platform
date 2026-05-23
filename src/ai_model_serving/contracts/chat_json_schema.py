from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

from ..errors import ServiceError
from .chat_common import _chat_policy

JSON_SCHEMA_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SCHEMA_VALUE_KEYS = {
    "additionalProperties",
    "items",
    "contains",
    "propertyNames",
    "unevaluatedItems",
    "unevaluatedProperties",
    "if",
    "then",
    "else",
    "not",
}
SCHEMA_ARRAY_KEYS = {
    "anyOf",
    "oneOf",
    "allOf",
    "prefixItems",
}
SCHEMA_MAP_VALUE_KEYS = {
    "properties",
    "$defs",
    "definitions",
    "dependentSchemas",
    "patternProperties",
}


def _schema_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    response_policy = _chat_policy(policy).get("response_format", {})
    if not isinstance(response_policy, dict):
        return {}
    schema_policy = response_policy.get("json_schema", {})
    return schema_policy if isinstance(schema_policy, dict) else {}


def _schema_limit(policy: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(policy.get(key, default))
    except (TypeError, ValueError):
        return default


def _schema_depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_schema_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_schema_depth(item) for item in value), default=0)
    return 1


def _iter_schema_objects(value: Any) -> Iterator[dict[str, Any]]:
    if not isinstance(value, dict):
        return
    yield value

    for key in SCHEMA_VALUE_KEYS:
        item = value.get(key)
        if isinstance(item, dict):
            yield from _iter_schema_objects(item)

    for key in SCHEMA_ARRAY_KEYS:
        items = value.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    yield from _iter_schema_objects(item)

    for key in SCHEMA_MAP_VALUE_KEYS:
        items = value.get(key)
        if isinstance(items, dict):
            for item in items.values():
                if isinstance(item, dict):
                    yield from _iter_schema_objects(item)


def _total_schema_string_length(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(len(str(key)) + _total_schema_string_length(item) for key, item in value.items())
    if isinstance(value, list):
        return sum(_total_schema_string_length(item) for item in value)
    return 0


def _validate_json_schema_subset(schema: dict[str, Any], *, policy: dict[str, Any]) -> None:
    disallowed = set(policy.get("disallowed_keywords", []))
    for obj in _iter_schema_objects(schema):
        if "$ref" not in obj:
            blocked = sorted(disallowed.intersection(obj))
        else:
            ref = obj.get("$ref")
            if not isinstance(ref, str) or not ref.startswith("#"):
                raise ServiceError(
                    "VALIDATION_ERROR",
                    "response_format.json_schema.schema only supports local $ref values that start with '#'.",
                    False,
                    422,
                )
            blocked = sorted(disallowed.intersection(obj))
        if blocked:
            raise ServiceError("VALIDATION_ERROR", f"response_format.json_schema.schema uses unsupported keyword(s): {', '.join(blocked)}.", False, 422)

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ServiceError(
            "VALIDATION_ERROR",
            "response_format.json_schema.schema must be a valid JSON Schema.",
            False,
            422,
        ) from exc

    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    max_schema_bytes = _schema_limit(policy, "max_schema_bytes", 16384)
    if len(encoded) > max_schema_bytes:
        raise ServiceError("VALIDATION_ERROR", f"response_format.json_schema.schema must be {max_schema_bytes} bytes or fewer.", False, 422)
    max_depth = _schema_limit(policy, "max_depth", 8)
    if _schema_depth(schema) > max_depth:
        raise ServiceError("VALIDATION_ERROR", f"response_format.json_schema.schema depth must be {max_depth} or fewer.", False, 422)
    if policy.get("require_root_object", True) is True and schema.get("type") != "object":
        raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.schema root type must be object.", False, 422)
    root_disallowed = set(policy.get("root_disallowed_keywords", ["anyOf"]))
    for keyword in root_disallowed:
        if keyword in schema:
            raise ServiceError("VALIDATION_ERROR", f"response_format.json_schema.schema root keyword is not supported in Phase 1: {keyword}.", False, 422)

    max_total_properties = _schema_limit(policy, "max_total_properties", 64)
    max_properties_per_object = _schema_limit(policy, "max_properties_per_object", 32)
    max_required = _schema_limit(policy, "max_required", 64)
    max_enum_values = _schema_limit(policy, "max_enum_values", 128)
    max_enum_string_length = _schema_limit(policy, "max_enum_string_length", 256)
    max_property_name_length = _schema_limit(policy, "max_property_name_length", 64)
    max_total_schema_string_length = _schema_limit(policy, "max_total_schema_string_length", 32768)
    total_properties = 0
    total_required = 0
    total_enum_values = 0

    if _total_schema_string_length(schema) > max_total_schema_string_length:
        raise ServiceError("VALIDATION_ERROR", f"response_format.json_schema.schema string content must be {max_total_schema_string_length} characters or fewer.", False, 422)

    for obj in _iter_schema_objects(schema):
        blocked = sorted(disallowed.intersection(obj))
        if blocked:
            raise ServiceError("VALIDATION_ERROR", f"response_format.json_schema.schema uses unsupported keyword(s): {', '.join(blocked)}.", False, 422)
        properties = obj.get("properties")
        is_object_schema = obj.get("type") == "object" or isinstance(properties, dict)
        if is_object_schema and policy.get("require_additional_properties_false", True) is True and obj.get("additionalProperties") is not False:
            raise ServiceError("VALIDATION_ERROR", "every object schema in response_format.json_schema.schema must set additionalProperties:false.", False, 422)
        if isinstance(properties, dict):
            count = len(properties)
            total_properties += count
            if count > max_properties_per_object:
                raise ServiceError("VALIDATION_ERROR", f"object schemas may define at most {max_properties_per_object} properties.", False, 422)
            for name in properties:
                if not isinstance(name, str) or len(name) > max_property_name_length:
                    raise ServiceError("VALIDATION_ERROR", f"schema property names must be strings of {max_property_name_length} chars or fewer.", False, 422)
        if is_object_schema and isinstance(properties, dict):
            required = obj.get("required")
            if not isinstance(required, list):
                raise ServiceError(
                    "VALIDATION_ERROR",
                    "every object schema with properties must define required as an array.",
                    False,
                    422,
                )
            if not all(isinstance(item, str) for item in required):
                raise ServiceError(
                    "VALIDATION_ERROR",
                    "response_format.json_schema.schema required entries must be strings.",
                    False,
                    422,
                )
            if set(required) != set(properties):
                raise ServiceError(
                    "VALIDATION_ERROR",
                    "every object schema must list all properties in required; use nullable type unions to emulate optional fields.",
                    False,
                    422,
                )
        required = obj.get("required")
        if isinstance(required, list):
            total_required += len(required)
        enum = obj.get("enum")
        if isinstance(enum, list):
            total_enum_values += len(enum)
            for item in enum:
                if isinstance(item, str) and len(item) > max_enum_string_length:
                    raise ServiceError("VALIDATION_ERROR", f"schema enum strings must be {max_enum_string_length} chars or fewer.", False, 422)
    if total_properties > max_total_properties:
        raise ServiceError("VALIDATION_ERROR", f"response_format.json_schema.schema may define at most {max_total_properties} total properties.", False, 422)
    if total_required > max_required:
        raise ServiceError("VALIDATION_ERROR", f"response_format.json_schema.schema may define at most {max_required} required entries.", False, 422)
    if total_enum_values > max_enum_values:
        raise ServiceError("VALIDATION_ERROR", f"response_format.json_schema.schema may define at most {max_enum_values} enum values.", False, 422)
