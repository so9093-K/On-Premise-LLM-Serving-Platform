from __future__ import annotations

import json
from typing import Any

from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for

from ..errors import ServiceError
from .chat_common import ChatResponseExpectations
from .chat_tools import _validate_tool_calls
from .common import ensure_object, is_int, is_number

def _validate_assistant_response_message(message: Any, *, choice_index: int, finish_reason: Any = None) -> None:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"chat upstream response choices[{choice_index}].message must contain an assistant message.")
    content = message.get("content")
    tool_calls = message.get("tool_calls")
    if isinstance(content, str):
        # vLLM은 tool을 사용하지 않았을 때 tool_calls: []를 반환하는 경우가 많다; 빈 리스트는 없는 것으로 취급한다.
        if tool_calls:
            _validate_tool_calls(tool_calls)
        return
    if content is None and tool_calls:
        _validate_tool_calls(tool_calls)
        return
    detail = f"chat upstream response choices[{choice_index}].message must contain assistant text content or tool_calls."
    # content 없이 finish_reason="length"인 경우는 생성이 답을 하나도 emit하기
    # 전에 max_tokens에 도달했다는 뜻이다 -- 이는 upstream 응답 오류가 아니라
    # request 쪽 budget 문제다. reasoning=true일 때 흔히 발생하는데, thinking
    # 단계가 budget 전체를 소비할 수 있기 때문이다. 원인과 해결책을 명시해서
    # 단순 재시도(동일한 max_tokens에서 동일하게 실패할 것이다)가 뻔한
    # 다음 단계가 되지 않도록 한다.
    if finish_reason == "length":
        detail += " The response was truncated by max_tokens before any content was emitted; increase max_tokens (reasoning requests need extra budget for the thinking phase)."
    raise ServiceError("UPSTREAM_SCHEMA_ERROR", detail)


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
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"chat upstream response choices[{choice_index}].message.content must be a JSON string for response_format={response_type}.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        detail = f"chat upstream response choices[{choice_index}].message.content is not valid JSON for response_format={response_type}; increase max_tokens or simplify the prompt/schema."
        if choice.get("finish_reason") == "length":
            detail += " The response may have been truncated by max_tokens."
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", detail) from exc
    if response_type != "json_schema":
        return
    schema = expectations.json_schema
    if not isinstance(schema, dict):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "Gateway response expectation is missing json_schema.")
    try:
        validator_cls = validator_for(schema)
        validator_cls.check_schema(schema)
        validator_cls(schema).validate(parsed)
    except SchemaError as exc:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "Gateway response expectation contains an invalid JSON Schema.") from exc
    except ValidationError as exc:
        detail = f"chat upstream response choices[{choice_index}].message.content does not match response_format.json_schema; simplify response_format.json_schema or increase max_tokens."
        if choice.get("finish_reason") == "length":
            detail += " The response may have been truncated by max_tokens."
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", detail) from exc
    except Exception as exc:
        raise ServiceError(
            "UPSTREAM_SCHEMA_ERROR", "upstream response could not be validated against response_format.json_schema; check error.debug for the validation reason.",
        ) from exc


def _validate_logprob_bytes(value: Any, *, context: str) -> None:
    if value is None:
        return
    if isinstance(value, list) and all(is_int(item) and 0 <= item <= 255 for item in value):
        return
    raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"{context}.bytes must be null or an array of byte integers.")


def _validate_top_logprob_item(item: Any, *, context: str) -> None:
    if not isinstance(item, dict):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"{context} must be an object.")
    if not isinstance(item.get("token"), str):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"{context}.token must be a string.")
    if not is_number(item.get("logprob")):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"{context}.logprob must be a number.")
    _validate_logprob_bytes(item.get("bytes"), context=context)


def _validate_logprob_item(item: Any, *, context: str) -> None:
    _validate_top_logprob_item(item, context=context)
    top = item.get("top_logprobs")
    if not isinstance(top, list):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"{context}.top_logprobs must be an array.")
    for index, top_item in enumerate(top):
        _validate_top_logprob_item(top_item, context=f"{context}.top_logprobs[{index}]")


def _validate_choice_logprobs(choice: dict[str, Any], *, choice_index: int) -> None:
    logprobs = choice.get("logprobs")
    if not isinstance(logprobs, dict):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"chat upstream response choices[{choice_index}].logprobs must be an object when logprobs=true.")
    for field in ("content", "refusal"):
        value = logprobs.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"choices[{choice_index}].logprobs.{field} must be null or an array.")
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
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"chat upstream response model must be {expected_model}.")
    if payload.get("object") != "chat.completion":
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "chat upstream response object must be chat.completion.")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "chat upstream response choices must be a non-empty array.")
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"chat upstream response choices[{index}] must be an object.")
        _validate_assistant_response_message(choice.get("message"), choice_index=index, finish_reason=choice.get("finish_reason"))
        if expectations is not None:
            _validate_response_json_content(choice, choice_index=index, expectations=expectations)
            if expectations.expect_logprobs and not expectations.stream:
                _validate_choice_logprobs(choice, choice_index=index)
    return payload
