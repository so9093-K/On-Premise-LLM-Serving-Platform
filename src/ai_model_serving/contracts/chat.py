from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from jsonschema.validators import validator_for

from ..errors import ServiceError
from .common import ensure_object, is_int, is_number, reject_unknown_fields
from .media import validate_message_content

CHAT_ROLES = {"system", "user", "assistant"}
TOOL_CHAT_ROLES = {"system", "user", "assistant", "tool"}
UNSUPPORTED_CHAT_FIELDS = {"tools", "tool_choice", "parallel_tool_calls"}
UNSUPPORTED_MESSAGE_FIELDS = {"tool_calls", "tool_call_id"}
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


@dataclass(frozen=True)
class ChatResponseExpectations:
    response_format_type: str | None
    json_schema: dict[str, Any] | None
    expect_logprobs: bool
    stream: bool


def _chat_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    return policy or {}


def _policy_tool_calling_enabled(policy: dict[str, Any] | None) -> bool:
    tool_policy = _chat_policy(policy).get("tool_calling", {})
    return isinstance(tool_policy, dict) and tool_policy.get("enabled") is True


def _policy_parallel_tools_enabled(policy: dict[str, Any] | None) -> bool:
    tool_policy = _chat_policy(policy).get("tool_calling", {})
    return isinstance(tool_policy, dict) and tool_policy.get("allow_parallel_tool_calls") is True



def _validate_stream_options(value: Any) -> None:
    if not isinstance(value, dict):
        raise ServiceError("VALIDATION_ERROR", "stream_options must be an object when provided.", False, 422)
    reject_unknown_fields(value, {"include_usage"}, "stream_options")
    if "include_usage" in value and not isinstance(value["include_usage"], bool):
        raise ServiceError("VALIDATION_ERROR", "stream_options.include_usage must be boolean when provided.", False, 422)


def _validate_reasoning(value: Any, *, policy: dict[str, Any] | None) -> None:
    reasoning_policy = _chat_policy(policy).get("reasoning", {})
    if not isinstance(reasoning_policy, dict) or reasoning_policy.get("enabled") is not True:
        raise ServiceError("VALIDATION_ERROR", "reasoning is not enabled for this model.", False, 422)
    if not isinstance(value, bool):
        raise ServiceError("VALIDATION_ERROR", "reasoning must be boolean when provided.", False, 422)


def _validate_stop(value: Any) -> None:
    if isinstance(value, str):
        return
    if isinstance(value, list) and 0 < len(value) <= 8 and all(isinstance(item, str) for item in value):
        return
    raise ServiceError("VALIDATION_ERROR", "stop must be a string or an array of up to 8 strings.", False, 422)


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


def _validate_response_format(value: Any, payload: dict[str, Any], policy: dict[str, Any] | None) -> None:
    response_policy = _chat_policy(policy).get("response_format", {})
    if not isinstance(response_policy, dict) or response_policy.get("enabled") is not True:
        raise ServiceError("VALIDATION_ERROR", "response_format is not enabled for this model.", False, 422)
    if not isinstance(value, dict):
        raise ServiceError("VALIDATION_ERROR", "response_format must be an object when provided.", False, 422)
    reject_unknown_fields(value, {"type", "json_schema"}, "response_format")
    allowed_types = set(response_policy.get("types", ["text", "json_object", "json_schema"]))
    response_type = value.get("type")
    if response_type not in allowed_types:
        raise ServiceError("VALIDATION_ERROR", f"response_format.type must be one of {sorted(allowed_types)}.", False, 422)
    if response_type in {"text", "json_object"} and "json_schema" in value:
        raise ServiceError("VALIDATION_ERROR", f"response_format.json_schema is only allowed when type=json_schema, not {response_type}.", False, 422)
    if response_type == "json_object":
        object_policy = response_policy.get("json_object", {})
        if isinstance(object_policy, dict) and object_policy.get("require_json_instruction") is True and not _messages_contain_json_instruction(payload):
            raise ServiceError("VALIDATION_ERROR", "response_format.type=json_object requires an explicit JSON instruction in messages.", False, 422)
    if response_type != "json_schema":
        return
    schema_policy = _schema_policy(policy)
    if schema_policy.get("enabled", True) is not True:
        raise ServiceError("VALIDATION_ERROR", "response_format.type=json_schema is not enabled for this model.", False, 422)
    json_schema = value.get("json_schema")
    if not isinstance(json_schema, dict):
        raise ServiceError("VALIDATION_ERROR", "response_format.json_schema must be an object when type=json_schema.", False, 422)
    reject_unknown_fields(json_schema, {"name", "description", "strict", "schema"}, "response_format.json_schema")
    name = json_schema.get("name")
    if not isinstance(name, str) or not JSON_SCHEMA_NAME_RE.fullmatch(name):
        raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.name must match ^[A-Za-z0-9_-]{1,64}$.", False, 422)
    if "description" in json_schema and not isinstance(json_schema["description"], str):
        raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.description must be a string when provided.", False, 422)
    strict_policy = schema_policy.get("strict", {}) if isinstance(schema_policy.get("strict", {}), dict) else {}
    if "strict" in json_schema:
        if not isinstance(json_schema["strict"], bool):
            raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.strict must be boolean when provided.", False, 422)
        if strict_policy.get("allowed", True) is not True:
            raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.strict is not enabled for this model.", False, 422)
    if strict_policy.get("require_true") is True and json_schema.get("strict") is not True:
        raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.strict must be true for this model.", False, 422)
    schema = json_schema.get("schema")
    if not isinstance(schema, dict):
        raise ServiceError("VALIDATION_ERROR", "response_format.json_schema.schema must be an object.", False, 422)
    _validate_json_schema_subset(schema, policy=schema_policy)


def _validate_logprobs(payload: dict[str, Any], policy: dict[str, Any] | None) -> None:
    chat_policy = _chat_policy(policy)
    logprobs_policy = chat_policy.get("logprobs", {})
    if "logprobs" in payload:
        if not isinstance(logprobs_policy, dict) or logprobs_policy.get("enabled") is not True:
            raise ServiceError("VALIDATION_ERROR", "logprobs is not enabled for this model.", False, 422)
        if not isinstance(payload["logprobs"], bool):
            raise ServiceError("VALIDATION_ERROR", "logprobs must be boolean when provided.", False, 422)
        if payload["logprobs"] and payload.get("stream") is True and logprobs_policy.get("allow_stream", True) is not True:
            raise ServiceError("VALIDATION_ERROR", "logprobs with stream=true is not enabled for this model.", False, 422)
    if "top_logprobs" not in payload:
        return
    top_policy = chat_policy.get("top_logprobs", {})
    if not is_int(payload["top_logprobs"]):
        raise ServiceError("VALIDATION_ERROR", "top_logprobs must be an integer when provided.", False, 422)
    if isinstance(top_policy, dict) and top_policy.get("requires_logprobs", True) is True and payload.get("logprobs") is not True:
        raise ServiceError("VALIDATION_ERROR", "top_logprobs requires logprobs=true, including when top_logprobs=0.", False, 422)
    min_value = int(top_policy.get("min", 0)) if isinstance(top_policy, dict) else 0
    max_value = int(top_policy.get("max", 10)) if isinstance(top_policy, dict) else 10
    if payload["top_logprobs"] < min_value or payload["top_logprobs"] > max_value:
        raise ServiceError("VALIDATION_ERROR", f"top_logprobs must be between {min_value} and {max_value}.", False, 422)


def _validate_logit_bias(value: Any, policy: dict[str, Any] | None) -> None:
    bias_policy = _chat_policy(policy).get("logit_bias", {})
    if not isinstance(bias_policy, dict) or bias_policy.get("enabled") is not True:
        raise ServiceError("VALIDATION_ERROR", "logit_bias is not enabled for this model.", False, 422)
    if not isinstance(value, dict):
        raise ServiceError("VALIDATION_ERROR", "logit_bias must be an object mapping served model tokenizer token ids to bias values.", False, 422)
    max_entries = int(bias_policy.get("max_entries", 256))
    if len(value) > max_entries:
        raise ServiceError("VALIDATION_ERROR", f"logit_bias may contain at most {max_entries} entries.", False, 422)
    min_bias = float(bias_policy.get("min_bias", -100))
    max_bias = float(bias_policy.get("max_bias", 100))
    token_id_min = int(bias_policy.get("token_id_min", 0))
    for token_id, bias in value.items():
        if not isinstance(token_id, str) or not token_id.isdecimal() or int(token_id) < token_id_min:
            raise ServiceError("VALIDATION_ERROR", "logit_bias keys must be non-negative integer strings for the served model tokenizer.", False, 422)
        if not is_number(bias) or bias < min_bias or bias > max_bias:
            raise ServiceError("VALIDATION_ERROR", f"logit_bias values must be numbers between {min_bias:g} and {max_bias:g}; token ids use the served model tokenizer, not OpenAI/tiktoken ids.", False, 422)


def _combination_mode(policy: dict[str, Any] | None, key: str) -> str:
    combinations = _chat_policy(policy).get("combinations", {})
    entry = combinations.get(key, {}) if isinstance(combinations, dict) else {}
    return str(entry.get("mode", "allow")) if isinstance(entry, dict) else "allow"


def _validate_parameter_combinations(payload: dict[str, Any], policy: dict[str, Any] | None) -> None:
    response_type = payload.get("response_format", {}).get("type") if isinstance(payload.get("response_format"), dict) else None
    checks = {
        "json_schema_with_tools": response_type == "json_schema" and "tools" in payload,
        "json_schema_with_reasoning": response_type == "json_schema" and payload.get("reasoning") is True,
        "json_schema_with_logit_bias": response_type == "json_schema" and "logit_bias" in payload,
        "logit_bias_with_tools": "logit_bias" in payload and "tools" in payload,
        "logprobs_with_stream": payload.get("logprobs") is True and payload.get("stream") is True,
    }
    for name, active in checks.items():
        if active and _combination_mode(policy, name) == "reject":
            raise ServiceError("VALIDATION_ERROR", f"request parameter combination is disabled by policy: {name}.", False, 422)


def _validate_tools(tools: Any, *, max_tools: int = 16) -> None:
    if not isinstance(tools, list) or not tools:
        raise ServiceError("VALIDATION_ERROR", "tools must be a non-empty array when provided.", False, 422)
    if len(tools) > max_tools:
        raise ServiceError("VALIDATION_ERROR", f"tools must contain {max_tools} items or fewer.", False, 422)
    names: set[str] = set()
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise ServiceError("VALIDATION_ERROR", f"tools[{index}] must be a function tool object.", False, 422)
        reject_unknown_fields(tool, {"type", "function"}, f"tools[{index}]")
        function = tool.get("function")
        if not isinstance(function, dict):
            raise ServiceError("VALIDATION_ERROR", f"tools[{index}].function must be an object.", False, 422)
        reject_unknown_fields(function, {"name", "description", "parameters", "strict"}, f"tools[{index}].function")
        name = function.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            raise ServiceError("VALIDATION_ERROR", f"tools[{index}].function.name must be a non-empty string of 64 chars or fewer.", False, 422)
        if name in names:
            raise ServiceError("VALIDATION_ERROR", f"duplicate tool name is not allowed: {name}.", False, 422)
        names.add(name)
        if "description" in function and not isinstance(function["description"], str):
            raise ServiceError("VALIDATION_ERROR", f"tools[{index}].function.description must be a string.", False, 422)
        if "parameters" in function and not isinstance(function["parameters"], dict):
            raise ServiceError("VALIDATION_ERROR", f"tools[{index}].function.parameters must be a JSON Schema object.", False, 422)
        if "strict" in function and not isinstance(function["strict"], bool):
            raise ServiceError("VALIDATION_ERROR", f"tools[{index}].function.strict must be boolean when provided.", False, 422)


def _validate_tool_choice(value: Any) -> None:
    if isinstance(value, str):
        if value in {"auto", "none", "required"}:
            return
        raise ServiceError("VALIDATION_ERROR", "tool_choice must be auto, none, required, or a function choice object.", False, 422)
    if not isinstance(value, dict) or value.get("type") != "function":
        raise ServiceError("VALIDATION_ERROR", "tool_choice must be auto, none, required, or a function choice object.", False, 422)
    reject_unknown_fields(value, {"type", "function"}, "tool_choice")
    function = value.get("function")
    if not isinstance(function, dict):
        raise ServiceError("VALIDATION_ERROR", "tool_choice.function must be an object.", False, 422)
    reject_unknown_fields(function, {"name"}, "tool_choice.function")
    if not isinstance(function.get("name"), str) or not function["name"].strip():
        raise ServiceError("VALIDATION_ERROR", "tool_choice.function.name must be a non-empty string.", False, 422)


def _tool_names(tools: Any) -> set[str]:
    if not isinstance(tools, list):
        return set()
    names: set[str] = set()
    for tool in tools:
        if isinstance(tool, dict):
            function = tool.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                names.add(function["name"])
    return names


def _validate_tool_choice_matches_tools(payload: dict[str, Any]) -> None:
    if "tool_choice" not in payload:
        return
    choice = payload["tool_choice"]
    tools = payload.get("tools")
    if choice == "none":
        return
    if not tools:
        raise ServiceError("VALIDATION_ERROR", "tool_choice requires a non-empty tools array unless it is 'none'.", False, 422)
    if isinstance(choice, dict):
        name = choice.get("function", {}).get("name") if isinstance(choice.get("function"), dict) else None
        if isinstance(name, str) and name not in _tool_names(tools):
            raise ServiceError("VALIDATION_ERROR", f"tool_choice.function.name must match one of the provided tools: {name}.", False, 422)


def _validate_tool_calls(tool_calls: Any) -> None:
    if not isinstance(tool_calls, list) or not tool_calls:
        raise ServiceError("VALIDATION_ERROR", "tool_calls must be a non-empty array when provided.", False, 422)
    for index, call in enumerate(tool_calls):
        if not isinstance(call, dict):
            raise ServiceError("VALIDATION_ERROR", f"tool_calls[{index}] must be an object.", False, 422)
        reject_unknown_fields(call, {"id", "type", "function"}, f"tool_calls[{index}]")
        if not isinstance(call.get("id"), str) or not call["id"].strip():
            raise ServiceError("VALIDATION_ERROR", f"tool_calls[{index}].id must be a non-empty string.", False, 422)
        if call.get("type") != "function":
            raise ServiceError("VALIDATION_ERROR", f"tool_calls[{index}].type must be function.", False, 422)
        function = call.get("function")
        if not isinstance(function, dict):
            raise ServiceError("VALIDATION_ERROR", f"tool_calls[{index}].function must be an object.", False, 422)
        reject_unknown_fields(function, {"name", "arguments"}, f"tool_calls[{index}].function")
        if not isinstance(function.get("name"), str) or not function["name"].strip():
            raise ServiceError("VALIDATION_ERROR", f"tool_calls[{index}].function.name must be a non-empty string.", False, 422)
        if not isinstance(function.get("arguments"), str):
            raise ServiceError("VALIDATION_ERROR", f"tool_calls[{index}].function.arguments must be a string.", False, 422)


def _validate_chat_parameters(payload: dict[str, Any], *, max_output_tokens: int | None, policy: dict[str, Any] | None) -> None:
    chat_policy = _chat_policy(policy)
    if chat_policy.get("allow_unlisted_parameters") is False:
        allowed = {"model", "messages"}.union(set(chat_policy.get("supported_parameters", [])))
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ServiceError("VALIDATION_ERROR", f"Unsupported chat completion field(s): {', '.join(unknown)}.", False, 422)

    if "temperature" in payload:
        value = payload["temperature"]
        if not is_number(value) or value < 0 or value > 2:
            raise ServiceError("VALIDATION_ERROR", "temperature must be a number between 0 and 2.", False, 422)
    if "max_tokens" in payload:
        value = payload["max_tokens"]
        if not is_int(value) or value < 1:
            raise ServiceError("VALIDATION_ERROR", "max_tokens must be an integer greater than or equal to 1.", False, 422)
        if max_output_tokens is not None and value > max_output_tokens:
            raise ServiceError("VALIDATION_ERROR", f"max_tokens must be less than or equal to {max_output_tokens}.", False, 422)
    if "top_p" in payload and (not is_number(payload["top_p"]) or payload["top_p"] <= 0 or payload["top_p"] > 1):
        raise ServiceError("VALIDATION_ERROR", "top_p must be a number in the interval (0, 1].", False, 422)
    if "top_k" in payload and (not is_int(payload["top_k"]) or payload["top_k"] < -1):
        raise ServiceError("VALIDATION_ERROR", "top_k must be -1 or a non-negative integer.", False, 422)
    if "min_p" in payload and (not is_number(payload["min_p"]) or payload["min_p"] < 0 or payload["min_p"] > 1):
        raise ServiceError("VALIDATION_ERROR", "min_p must be a number between 0 and 1.", False, 422)
    for field in ("presence_penalty", "frequency_penalty"):
        if field in payload and (not is_number(payload[field]) or payload[field] < -2 or payload[field] > 2):
            raise ServiceError("VALIDATION_ERROR", f"{field} must be a number between -2 and 2.", False, 422)
    if "repetition_penalty" in payload and (not is_number(payload["repetition_penalty"]) or payload["repetition_penalty"] <= 0 or payload["repetition_penalty"] > 2):
        raise ServiceError("VALIDATION_ERROR", "repetition_penalty must be a number greater than 0 and less than or equal to 2.", False, 422)
    if "seed" in payload and (not is_int(payload["seed"]) or payload["seed"] < 0):
        raise ServiceError("VALIDATION_ERROR", "seed must be a non-negative integer.", False, 422)
    if "n" in payload:
        n = payload["n"]
        max_n = int(chat_policy.get("max_n", 1))
        if not is_int(n) or n < 1 or n > max_n:
            raise ServiceError("VALIDATION_ERROR", f"n must be an integer between 1 and {max_n}.", False, 422)
    if "stop" in payload:
        _validate_stop(payload["stop"])
    if "logprobs" in payload or "top_logprobs" in payload:
        _validate_logprobs(payload, policy)
    if "logit_bias" in payload:
        _validate_logit_bias(payload["logit_bias"], policy)


def _allowed_message_fields(role: str, *, tool_enabled: bool) -> set[str]:
    if role == "tool":
        return {"role", "content", "tool_call_id", "name"}
    if role == "assistant" and tool_enabled:
        return {"role", "content", "tool_calls", "name"}
    return {"role", "content", "name"}


def validate_chat_request(
    payload: Any,
    *,
    expected_model: str,
    max_output_tokens: int | None = None,
    allowed_input_modalities: tuple[str, ...] = ("text",),
    max_image_inputs: int = 0,
    allowed_image_url_schemes: tuple[str, ...] = (),
    max_image_bytes: int = 0,
    max_image_pixels: int = 0,
    allowed_image_mime_types: tuple[str, ...] = (),
    request_parameter_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = ensure_object(payload)
    if payload.get("model") != expected_model:
        raise ServiceError("VALIDATION_ERROR", f"model must be {expected_model}.", False, 422)
    if "stream" in payload and not isinstance(payload["stream"], bool):
        raise ServiceError("VALIDATION_ERROR", "stream must be boolean when provided.", False, 422)
    if "stream_options" in payload:
        _validate_stream_options(payload["stream_options"])
        if payload.get("stream") is not True:
            raise ServiceError("VALIDATION_ERROR", "stream_options may only be provided when stream=true.", False, 422)
    if "reasoning" in payload:
        _validate_reasoning(payload["reasoning"], policy=request_parameter_policy)

    tool_enabled = _policy_tool_calling_enabled(request_parameter_policy)
    if not tool_enabled:
        unsupported_fields = sorted(field for field in UNSUPPORTED_CHAT_FIELDS if field in payload)
        if unsupported_fields:
            names = ", ".join(unsupported_fields)
            raise ServiceError("VALIDATION_ERROR", f"Unsupported chat completion field(s): {names}.", False, 422)
    else:
        tool_policy = _chat_policy(request_parameter_policy).get("tool_calling", {})
        max_tools = int(tool_policy.get("max_tools", 16)) if isinstance(tool_policy, dict) else 16
        if "tools" in payload:
            _validate_tools(payload["tools"], max_tools=max_tools)
        if "tool_choice" in payload:
            _validate_tool_choice(payload["tool_choice"])
            _validate_tool_choice_matches_tools(payload)
        if "parallel_tool_calls" in payload:
            if not isinstance(payload["parallel_tool_calls"], bool):
                raise ServiceError("VALIDATION_ERROR", "parallel_tool_calls must be boolean.", False, 422)
            if payload["parallel_tool_calls"] and not _policy_parallel_tools_enabled(request_parameter_policy):
                raise ServiceError("VALIDATION_ERROR", "parallel_tool_calls=true is not enabled for this model.", False, 422)

    _validate_chat_parameters(payload, max_output_tokens=max_output_tokens, policy=request_parameter_policy)

    allowed_modalities = set(allowed_input_modalities)
    allowed_schemes = set(allowed_image_url_schemes)
    allowed_mime_types = set(allowed_image_mime_types)
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ServiceError("VALIDATION_ERROR", "messages must be a non-empty array.", False, 422)
    image_count = 0
    allowed_roles = TOOL_CHAT_ROLES if tool_enabled else CHAT_ROLES
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ServiceError("VALIDATION_ERROR", f"messages[{index}] must be an object.", False, 422)
        if not tool_enabled:
            message_unsupported_fields = sorted(field for field in UNSUPPORTED_MESSAGE_FIELDS if field in message)
            if message_unsupported_fields:
                names = ", ".join(message_unsupported_fields)
                raise ServiceError(
                    "VALIDATION_ERROR",
                    f"messages[{index}] contains unsupported tool-calling field(s): {names}.",
                    False,
                    422,
                )
        role = message.get("role")
        if role not in allowed_roles:
            raise ServiceError(
                "VALIDATION_ERROR",
                f"messages[{index}].role must be one of {sorted(allowed_roles)}.",
                False,
                422,
            )
        reject_unknown_fields(message, _allowed_message_fields(role, tool_enabled=tool_enabled), f"messages[{index}]")
        if "name" in message and (not isinstance(message["name"], str) or not message["name"].strip()):
            raise ServiceError("VALIDATION_ERROR", f"messages[{index}].name must be a non-empty string when provided.", False, 422)
        if role == "tool":
            if not isinstance(message.get("tool_call_id"), str) or not message["tool_call_id"].strip():
                raise ServiceError("VALIDATION_ERROR", f"messages[{index}].tool_call_id is required for tool messages.", False, 422)
            if not isinstance(message.get("content"), str):
                raise ServiceError("VALIDATION_ERROR", f"messages[{index}].content must be a string for tool messages.", False, 422)
            continue
        if role == "assistant" and "tool_calls" in message:
            if not tool_enabled:
                raise ServiceError("VALIDATION_ERROR", f"messages[{index}] contains unsupported tool_calls.", False, 422)
            _validate_tool_calls(message["tool_calls"])
            if message.get("content") is None:
                continue
        image_count += validate_message_content(
            message.get("content"),
            allowed_modalities=allowed_modalities,
            max_image_inputs=max_image_inputs,
            allowed_image_url_schemes=allowed_schemes,
            max_image_bytes=max_image_bytes,
            max_image_pixels=max_image_pixels,
            allowed_image_mime_types=allowed_mime_types,
        )
    if image_count > max_image_inputs:
        raise ServiceError("VALIDATION_ERROR", f"at most {max_image_inputs} image content part(s) are allowed per request.", False, 422)
    if "response_format" in payload:
        _validate_response_format(payload["response_format"], payload, request_parameter_policy)
    _validate_parameter_combinations(payload, request_parameter_policy)
    return payload


def _validate_assistant_response_message(message: Any, *, choice_index: int) -> None:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"chat upstream response choices[{choice_index}].message must contain an assistant message.", True, 502)
    content = message.get("content")
    tool_calls = message.get("tool_calls")
    if isinstance(content, str):
        # vLLM often returns tool_calls: [] when no tools were used; treat empty list as absent.
        if tool_calls:
            _validate_tool_calls(tool_calls)
        return
    if content is None and tool_calls:
        _validate_tool_calls(tool_calls)
        return
    raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"chat upstream response choices[{choice_index}].message must contain assistant text content or tool_calls.", True, 502)


def _validate_response_json_content(
    choice: dict[str, Any],
    *,
    choice_index: int,
    expectations: ChatResponseExpectations,
) -> None:
    if choice.get("finish_reason") == "tool_calls":
        return
    response_type = expectations.response_format_type
    if response_type not in {"json_object", "json_schema"}:
        return
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"chat upstream response choices[{choice_index}].message.content must be a JSON string for response_format={response_type}.", True, 502)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        detail = f"chat upstream response choices[{choice_index}].message.content is not valid JSON for response_format={response_type}."
        if choice.get("finish_reason") == "length":
            detail += " The response may have been truncated by max_tokens."
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", detail, True, 502) from exc
    if response_type != "json_schema":
        return
    schema = expectations.json_schema
    if not isinstance(schema, dict):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "Gateway response expectation is missing json_schema.", True, 502)
    try:
        validator_cls = validator_for(schema)
        validator_cls.check_schema(schema)
        validator_cls(schema).validate(parsed)
    except SchemaError as exc:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "Gateway response expectation contains an invalid JSON Schema.", True, 502) from exc
    except ValidationError as exc:
        detail = f"chat upstream response choices[{choice_index}].message.content does not match response_format.json_schema."
        if choice.get("finish_reason") == "length":
            detail += " The response may have been truncated by max_tokens."
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", detail, True, 502) from exc
    except Exception as exc:
        raise ServiceError(
            "UPSTREAM_SCHEMA_ERROR",
            "upstream response could not be validated against response_format.json_schema.",
            True,
            502,
        ) from exc


def _validate_logprob_bytes(value: Any, *, context: str) -> None:
    if value is None:
        return
    if isinstance(value, list) and all(is_int(item) and 0 <= item <= 255 for item in value):
        return
    raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"{context}.bytes must be null or an array of byte integers.", True, 502)


def _validate_top_logprob_item(item: Any, *, context: str) -> None:
    if not isinstance(item, dict):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"{context} must be an object.", True, 502)
    if not isinstance(item.get("token"), str):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"{context}.token must be a string.", True, 502)
    if not is_number(item.get("logprob")):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"{context}.logprob must be a number.", True, 502)
    _validate_logprob_bytes(item.get("bytes"), context=context)


def _validate_logprob_item(item: Any, *, context: str) -> None:
    _validate_top_logprob_item(item, context=context)
    top = item.get("top_logprobs")
    if not isinstance(top, list):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"{context}.top_logprobs must be an array.", True, 502)
    for index, top_item in enumerate(top):
        _validate_top_logprob_item(top_item, context=f"{context}.top_logprobs[{index}]")


def _validate_choice_logprobs(choice: dict[str, Any], *, choice_index: int) -> None:
    logprobs = choice.get("logprobs")
    if not isinstance(logprobs, dict):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"chat upstream response choices[{choice_index}].logprobs must be an object when logprobs=true.", True, 502)
    for field in ("content", "refusal"):
        value = logprobs.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"choices[{choice_index}].logprobs.{field} must be null or an array.", True, 502)
        for item_index, item in enumerate(value):
            _validate_logprob_item(item, context=f"choices[{choice_index}].logprobs.{field}[{item_index}]")


def validate_chat_response(
    payload: Any,
    *,
    expected_model: str,
    expectations: ChatResponseExpectations | None = None,
) -> dict[str, Any]:
    payload = ensure_object(payload)
    if payload.get("model") != expected_model:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"chat upstream response model must be {expected_model}.", True, 502)
    if payload.get("object") != "chat.completion":
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "chat upstream response object must be chat.completion.", True, 502)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "chat upstream response choices must be a non-empty array.", True, 502)
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"chat upstream response choices[{index}] must be an object.", True, 502)
        _validate_assistant_response_message(choice.get("message"), choice_index=index)
        if expectations is not None:
            _validate_response_json_content(choice, choice_index=index, expectations=expectations)
            if expectations.expect_logprobs and not expectations.stream:
                _validate_choice_logprobs(choice, choice_index=index)
    return payload
