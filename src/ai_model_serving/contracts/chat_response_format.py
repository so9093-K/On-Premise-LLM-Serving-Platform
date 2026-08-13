from __future__ import annotations

from typing import Any

from ..errors import ServiceError
from .chat_common import _chat_policy
from .chat_json_schema import JSON_SCHEMA_NAME_RE, _schema_policy, _validate_json_schema_subset
from .common import field_param, reject_unknown_fields

def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _messages_contain_json_instruction(payload: dict[str, Any]) -> bool:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return False
    return any("json" in _message_text(message.get("content")).lower() for message in messages if isinstance(message, dict))


@field_param("response_format")
def _validate_response_format(value: Any, payload: dict[str, Any], policy: dict[str, Any] | None) -> None:
    response_policy = _chat_policy(policy).get("response_format", {})
    if not isinstance(response_policy, dict) or response_policy.get("enabled") is not True:
        raise ServiceError("VALIDATION_ERROR", "response_format is not enabled for this model; omit response_format or switch to a profile that supports structured responses.", False, 422)
    if not isinstance(value, dict):
        raise ServiceError("VALIDATION_ERROR", "response_format must be an object when provided.", False, 422)
    reject_unknown_fields(value, {"type", "json_schema"}, "response_format")
    allowed_types = set(response_policy.get("types", ["text", "json_object", "json_schema"]))
    response_type = value.get("type")
    if response_type not in allowed_types:
        allowed = ", ".join(sorted(allowed_types))
        raise ServiceError("VALIDATION_ERROR", f"response_format.type is {response_type}; use one of: {allowed}.", False, 422)
    if response_type in {"text", "json_object"} and "json_schema" in value:
        raise ServiceError("VALIDATION_ERROR", f"response_format.json_schema is only allowed when type=json_schema, not {response_type}.", False, 422)
    if response_type == "json_object":
        object_policy = response_policy.get("json_object", {})
        if isinstance(object_policy, dict) and object_policy.get("require_json_instruction") is True and not _messages_contain_json_instruction(payload):
            raise ServiceError("VALIDATION_ERROR", "response_format.type=json_object requires an explicit JSON instruction in messages; add a user or system message such as 'Return JSON.'.", False, 422)
    if response_type != "json_schema":
        return
    schema_policy = _schema_policy(policy)
    if schema_policy.get("enabled", True) is not True:
        raise ServiceError("VALIDATION_ERROR", "response_format.type=json_schema is not enabled for this model; use response_format.type=text/json_object or switch profiles.", False, 422)
    json_schema = value.get("json_schema")
    if not isinstance(json_schema, dict):
        raise ServiceError("VALIDATION_ERROR", "response_format.json_schema must be an object when type=json_schema.", False, 422)
    reject_unknown_fields(json_schema, {"name", "description", "strict", "schema"}, "response_format.json_schema")
    name = json_schema.get("name")
    if not isinstance(name, str) or not JSON_SCHEMA_NAME_RE.fullmatch(name):
        raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.name must match ^[A-Za-z0-9_-]{1,64}$; use letters, numbers, '_' or '-' only.", False, 422)
    if "description" in json_schema and not isinstance(json_schema["description"], str):
        raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.description must be a string when provided.", False, 422)
    strict_policy = schema_policy.get("strict", {}) if isinstance(schema_policy.get("strict", {}), dict) else {}
    if "strict" in json_schema:
        if not isinstance(json_schema["strict"], bool):
            raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.strict must be boolean when provided.", False, 422)
        if strict_policy.get("allowed", True) is not True:
            raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.strict is not enabled for this model; remove strict or switch profiles.", False, 422)
    if strict_policy.get("require_true") is True and json_schema.get("strict") is not True:
        raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.strict must be true for this model; set strict=true.", False, 422)
    schema = json_schema.get("schema")
    if not isinstance(schema, dict):
        raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.schema must be an object.", False, 422)
    _validate_json_schema_subset(schema, policy=schema_policy)
