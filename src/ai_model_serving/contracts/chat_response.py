from __future__ import annotations

import json
from typing import Any

from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for

from ..errors import ServiceError
from .chat_common import ChatResponseExpectations
from .chat_tools import _validate_tool_calls
from .common import ensure_response_object, is_int, is_number


class RetryableStructuredOutputError(ServiceError):
    """Runtime이 생성한 JSON 본문만 다시 생성해 볼 가치가 있음을 표시한다."""


def _validate_assistant_response_message(
    message: Any,
    *,
    choice_index: int,
    finish_reason: Any = None,
    expectations: ChatResponseExpectations | None = None,
) -> None:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", f"chat upstream response choices[{choice_index}].message must contain an assistant message.")
    content = message.get("content")
    tool_calls = message.get("tool_calls")
    if content is not None and not isinstance(content, str):
        raise ServiceError(
            "UPSTREAM_RESPONSE_INVALID",
            f"chat upstream response choices[{choice_index}].message.content must be a string or null.",
        )
    for field in ("reasoning", "reasoning_content"):
        if field in message and message[field] is not None and not isinstance(message[field], str):
            raise ServiceError(
                "UPSTREAM_RESPONSE_INVALID",
                f"chat upstream response choices[{choice_index}].message.{field} must be a string or null.",
            )
    reasoning = message.get("reasoning") or message.get("reasoning_content")
    has_tool_calls = isinstance(tool_calls, list) and bool(tool_calls)
    if tool_calls not in (None, []) and not has_tool_calls:
        raise ServiceError(
            "UPSTREAM_RESPONSE_INVALID",
            f"chat upstream response choices[{choice_index}].message.tool_calls must be a non-empty array when provided.",
        )
    if has_tool_calls:
        try:
            _validate_tool_calls(tool_calls)
        except ServiceError as exc:
            # _validate_tool_calls는 client request에도 쓰이므로 VALIDATION_ERROR를
            # 만든다. 여기서는 runtime이 만든 응답이 원인이므로 502로 다시 분류한다.
            raise ServiceError(
                "UPSTREAM_RESPONSE_INVALID",
                f"chat upstream response choices[{choice_index}].message.tool_calls is invalid: {exc.message}",
            ) from exc
    if finish_reason == "tool_calls" and not has_tool_calls:
        raise ServiceError(
            "UPSTREAM_RESPONSE_INVALID",
            f"chat upstream response choices[{choice_index}] ended with finish_reason=tool_calls but returned no tool_calls.",
        )
    if has_tool_calls and finish_reason != "tool_calls":
        raise ServiceError(
            "UPSTREAM_RESPONSE_INVALID",
            f"chat upstream response choices[{choice_index}] returned tool_calls without finish_reason=tool_calls.",
        )
    if expectations is not None:
        if has_tool_calls:
            names = [call["function"]["name"] for call in tool_calls]
            if not expectations.allowed_tool_names or any(
                name not in expectations.allowed_tool_names for name in names
            ):
                raise ServiceError(
                    "UPSTREAM_RESPONSE_INVALID",
                    f"chat upstream response choices[{choice_index}] returned a function that was not provided in tools.",
                )
            if expectations.tool_choice == "none":
                raise ServiceError(
                    "UPSTREAM_RESPONSE_INVALID",
                    f"chat upstream response choices[{choice_index}] returned tool_calls for tool_choice=none.",
                )
            if expectations.tool_choice_name is not None and any(
                name != expectations.tool_choice_name for name in names
            ):
                raise ServiceError(
                    "UPSTREAM_RESPONSE_INVALID",
                    f"chat upstream response choices[{choice_index}] did not honor the named tool_choice.",
                )
            if not expectations.parallel_tool_calls and len(tool_calls) > 1:
                raise ServiceError(
                    "UPSTREAM_RESPONSE_INVALID",
                    f"chat upstream response choices[{choice_index}] returned parallel tool calls when parallel_tool_calls=false.",
                )
        elif expectations.tool_choice in {"required", "named"}:
            raise ServiceError(
                "UPSTREAM_RESPONSE_INVALID",
                f"chat upstream response choices[{choice_index}] returned no tool_calls for tool_choice={expectations.tool_choice}.",
            )
    if isinstance(content, str) or has_tool_calls:
        return
    # Reasoning runtimes can exhaust max_tokens before emitting final content.
    # The reasoning text is still a valid, explicitly truncated completion and
    # must not be rewritten as a retryable upstream failure. A completely empty
    # result remains an upstream response error because the Gateway has no model
    # output to return and the same shape is also produced by runtime failures.
    if finish_reason == "length" and isinstance(reasoning, str) and reasoning:
        return
    detail = f"chat upstream response choices[{choice_index}].message must contain assistant text content or tool_calls."
    if finish_reason == "length":
        detail += " The response was truncated by max_tokens before any content was emitted; increase max_tokens (reasoning requests need extra budget for the thinking phase)."
    raise ServiceError("UPSTREAM_RESPONSE_INVALID", detail)


def _validate_response_json_content(
    choice: dict[str, Any],
    *,
    choice_index: int,
    expectations: ChatResponseExpectations,
) -> None:
    message = choice.get("message")
    if isinstance(message, dict) and message.get("tool_calls"):
        return
    response_type = expectations.response_format_type
    if response_type not in {"json_object", "json_schema"}:
        return
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", f"chat upstream response choices[{choice_index}].message.content must be a JSON string for response_format={response_type}.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        detail = f"chat upstream response choices[{choice_index}].message.content is not valid JSON for response_format={response_type}; increase max_tokens or simplify the prompt/schema."
        if choice.get("finish_reason") == "length":
            detail += " The response may have been truncated by max_tokens."
        raise RetryableStructuredOutputError("STRUCTURED_OUTPUT_INVALID", detail) from exc
    if response_type != "json_schema":
        return
    schema = expectations.json_schema
    if not isinstance(schema, dict):
        raise ServiceError(
            "INTERNAL_ERROR",
            "Gateway response validation configuration is invalid.",
            diagnostic_code="RESPONSE_EXPECTATION_INVALID",
        )
    try:
        validator_cls = validator_for(schema)
        validator_cls.check_schema(schema)
        validator_cls(schema).validate(parsed)
    except SchemaError as exc:
        raise ServiceError(
            "INTERNAL_ERROR",
            "Gateway response validation configuration is invalid.",
            diagnostic_code="RESPONSE_EXPECTATION_INVALID",
        ) from exc
    except ValidationError as exc:
        detail = f"chat upstream response choices[{choice_index}].message.content does not match response_format.json_schema; simplify response_format.json_schema or increase max_tokens."
        if choice.get("finish_reason") == "length":
            detail += " The response may have been truncated by max_tokens."
        raise RetryableStructuredOutputError("STRUCTURED_OUTPUT_INVALID", detail) from exc
    except Exception as exc:
        raise ServiceError(
            "INTERNAL_ERROR",
            "Gateway response validation configuration is invalid.",
            diagnostic_code="RESPONSE_EXPECTATION_INVALID",
        ) from exc


def _validate_logprob_bytes(value: Any, *, context: str) -> None:
    if value is None:
        return
    if isinstance(value, list) and all(is_int(item) and 0 <= item <= 255 for item in value):
        return
    raise ServiceError("UPSTREAM_RESPONSE_INVALID", f"{context}.bytes must be null or an array of byte integers.")


def _validate_top_logprob_item(item: Any, *, context: str) -> None:
    if not isinstance(item, dict):
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", f"{context} must be an object.")
    if not isinstance(item.get("token"), str):
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", f"{context}.token must be a string.")
    if not is_number(item.get("logprob")):
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", f"{context}.logprob must be a number.")
    _validate_logprob_bytes(item.get("bytes"), context=context)


def _validate_logprob_item(item: Any, *, context: str) -> None:
    _validate_top_logprob_item(item, context=context)
    top = item.get("top_logprobs")
    if not isinstance(top, list):
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", f"{context}.top_logprobs must be an array.")
    for index, top_item in enumerate(top):
        _validate_top_logprob_item(top_item, context=f"{context}.top_logprobs[{index}]")


def _validate_choice_logprobs(choice: dict[str, Any], *, choice_index: int) -> None:
    logprobs = choice.get("logprobs")
    if not isinstance(logprobs, dict):
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", f"chat upstream response choices[{choice_index}].logprobs must be an object when logprobs=true.")
    for field in ("content", "refusal"):
        value = logprobs.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            raise ServiceError("UPSTREAM_RESPONSE_INVALID", f"choices[{choice_index}].logprobs.{field} must be null or an array.")
        for item_index, item in enumerate(value):
            _validate_logprob_item(item, context=f"choices[{choice_index}].logprobs.{field}[{item_index}]")


def validate_chat_response(
    payload: Any,
    *,
    expected_model: str,
    expectations: ChatResponseExpectations | None = None,
) -> dict[str, Any]:
    payload = ensure_response_object(payload)
    if payload.get("model") != expected_model:
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", f"chat upstream response model must be {expected_model}.")
    if payload.get("object") != "chat.completion":
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", "chat upstream response object must be chat.completion.")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", "chat upstream response choices must be a non-empty array.")
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            raise ServiceError("UPSTREAM_RESPONSE_INVALID", f"chat upstream response choices[{index}] must be an object.")
        _validate_assistant_response_message(
            choice.get("message"),
            choice_index=index,
            finish_reason=choice.get("finish_reason"),
            expectations=expectations,
        )
        if expectations is not None:
            _validate_response_json_content(choice, choice_index=index, expectations=expectations)
            if expectations.expect_logprobs and not expectations.stream:
                _validate_choice_logprobs(choice, choice_index=index)
    return payload
