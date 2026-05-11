from __future__ import annotations

from typing import Any

from ..errors import ServiceError
from .common import ensure_object, is_int, is_number, reject_unknown_fields
from .media import validate_message_content

CHAT_ROLES = {"system", "user", "assistant"}
TOOL_CHAT_ROLES = {"system", "user", "assistant", "tool"}
UNSUPPORTED_CHAT_FIELDS = {"tools", "tool_choice", "parallel_tool_calls"}
UNSUPPORTED_MESSAGE_FIELDS = {"tool_calls", "tool_call_id"}


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


def _validate_stop(value: Any) -> None:
    if isinstance(value, str):
        return
    if isinstance(value, list) and 0 < len(value) <= 8 and all(isinstance(item, str) for item in value):
        return
    raise ServiceError("VALIDATION_ERROR", "stop must be a string or an array of up to 8 strings.", False, 422)


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


def validate_chat_response(payload: Any, *, expected_model: str) -> dict[str, Any]:
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
    return payload
