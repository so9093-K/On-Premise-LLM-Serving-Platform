from __future__ import annotations

from typing import Any

from ..errors import ServiceError
from .chat_common import (
    CHAT_ROLES,
    TOOL_CHAT_ROLES,
    UNSUPPORTED_CHAT_FIELDS,
    UNSUPPORTED_MESSAGE_FIELDS,
    _chat_policy,
    _policy_parallel_tools_enabled,
    _policy_tool_calling_enabled,
)
from .chat_response_format import _validate_response_format
from .chat_tools import (
    _validate_tool_calls,
    _validate_tool_choice,
    _validate_tool_choice_matches_tools,
    _validate_tools,
)
from .common import ensure_object, is_int, is_number, reject_unknown_fields
from .media import validate_message_content

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
    max_audio_inputs: int = 0,
    allowed_audio_formats: tuple[str, ...] = (),
    max_audio_bytes: int = 0,
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
    allowed_audio = set(allowed_audio_formats)
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ServiceError("VALIDATION_ERROR", "messages must be a non-empty array.", False, 422)
    image_count = 0
    audio_count = 0
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
        message_images, message_audio = validate_message_content(
            message.get("content"),
            allowed_modalities=allowed_modalities,
            max_image_inputs=max_image_inputs,
            allowed_image_url_schemes=allowed_schemes,
            max_image_bytes=max_image_bytes,
            max_image_pixels=max_image_pixels,
            allowed_image_mime_types=allowed_mime_types,
            max_audio_inputs=max_audio_inputs,
            allowed_audio_formats=allowed_audio,
            max_audio_bytes=max_audio_bytes,
        )
        image_count += message_images
        audio_count += message_audio
    if image_count > max_image_inputs:
        raise ServiceError("VALIDATION_ERROR", f"at most {max_image_inputs} image content part(s) are allowed per request.", False, 422)
    if audio_count > max_audio_inputs:
        raise ServiceError("VALIDATION_ERROR", f"at most {max_audio_inputs} audio content part(s) are allowed per request.", False, 422)
    if "response_format" in payload:
        _validate_response_format(payload["response_format"], payload, request_parameter_policy)
    _validate_parameter_combinations(payload, request_parameter_policy)
    return payload
