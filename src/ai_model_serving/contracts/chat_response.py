from __future__ import annotations

import json
from typing import Any

from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for

from ..errors import ServiceError
from .chat_common import ChatResponseExpectations
from .chat_tools import _validate_tool_calls
from .common import ensure_object, is_int, is_number

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
        detail = f"chat upstream response choices[{choice_index}].message.content is not valid JSON for response_format={response_type}; increase max_tokens or simplify the prompt/schema."
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
        detail = f"chat upstream response choices[{choice_index}].message.content does not match response_format.json_schema; simplify response_format.json_schema or increase max_tokens."
        if choice.get("finish_reason") == "length":
            detail += " The response may have been truncated by max_tokens."
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", detail, True, 502) from exc
    except Exception as exc:
        raise ServiceError(
            "UPSTREAM_SCHEMA_ERROR",
            "upstream response could not be validated against response_format.json_schema; check error.debug for the validation reason.",
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
