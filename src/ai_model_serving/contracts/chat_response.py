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


# 공개 응답이 담을 수 있는 키는 specs/schemas/chat_completion_response.schema.json이
# 소유한다. 여기 목록을 다시 적으면 schema와 코드가 갈라지고, 갈라진 쪽이 실제
# 동작이 된다.
#
# 이 좁히기가 없으면 런타임이 붙인 것이 그대로 공개 API로 나간다. 실제로 mlx-vlm의
# timings(peak_memory, draft_kind, draft_rounds)가 나갔고, message에는 OpenAI가
# 요청 쪽에서만 쓰는 tool_call_id와 name이 null로 실렸다. /docs는 선언된 모양을
# 보여주므로 문서와 실제가 달랐다.
_RESPONSE_SCHEMA_NAME = "chat_completion_response.schema.json"


def _declared_keys() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    from ..openapi_contracts import load_contract_schema

    schema = load_contract_schema(_RESPONSE_SCHEMA_NAME)
    choice = schema["properties"]["choices"]["items"]
    return (
        frozenset(schema["properties"]),
        frozenset(choice["properties"]),
        frozenset(choice["properties"]["message"]["properties"]),
    )


def project_to_public_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """선언된 키만 남긴다. 런타임이 덧붙인 것은 공개 API로 내보내지 않는다."""
    top, choice_keys, message_keys = _declared_keys()
    projected = {name: value for name, value in payload.items() if name in top}
    choices = []
    for choice in payload.get("choices") or []:
        if not isinstance(choice, dict):
            choices.append(choice)
            continue
        narrowed = {name: value for name, value in choice.items() if name in choice_keys}
        message = choice.get("message")
        if isinstance(message, dict):
            narrowed["message"] = {
                name: value for name, value in message.items() if name in message_keys
            }
        choices.append(narrowed)
    if choices:
        projected["choices"] = choices
    return projected


# 스트리밍 chunk의 delta가 담을 수 있는 키. chunk는 chat.completion.chunk이고
# 응답 schema는 chat.completion을 선언하므로, message가 아니라 delta로 온다.
# OpenAI의 delta는 role/content/tool_calls/refusal이고 이 플랫폼은 reasoning
# 확장을 더 광고한다 -- message에 선언한 것과 같은 집합을 쓴다.
def project_stream_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    """SSE chunk를 공개 계약으로 좁힌다.

    Gateway는 오랫동안 스트리밍 바이트를 그대로 중계했다. 그 편이 빠르고 stream을
    망가뜨릴 위험이 없지만, 런타임이 붙인 것이 전부 공개 API로 나간다. 실측에서
    mlx-vlm은 17개 chunk 전부에 timings(peak_memory, draft_kind, draft_rounds)를
    실었고, delta에는 OpenAI가 요청 쪽에서만 쓰는 name과 tool_call_id가 있었다.
    payload의 25%, chunk당 100바이트가 내부 상태였다.

    비스트리밍만 좁히면 두 경로가 서로 다른 계약을 내보낸다. 그 불일치가 균일한
    누출보다 나쁘다.
    """
    top, choice_keys, message_keys = _declared_keys()
    projected = {name: value for name, value in chunk.items() if name in top}
    choices = []
    for choice in chunk.get("choices") or []:
        if not isinstance(choice, dict):
            choices.append(choice)
            continue
        narrowed = {name: value for name, value in choice.items() if name in choice_keys or name == "delta"}
        delta = choice.get("delta")
        if isinstance(delta, dict):
            narrowed["delta"] = {
                name: value for name, value in delta.items() if name in message_keys
            }
        choices.append(narrowed)
    if choices:
        projected["choices"] = choices
    return projected


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
    return project_to_public_contract(payload)
