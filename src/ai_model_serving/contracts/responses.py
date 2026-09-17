from __future__ import annotations

from typing import Any

from ..errors import ServiceError
from .chat_common import _chat_policy
from .chat_response_format import _validate_response_format
from .common import ensure_request_object, is_int, is_number, reject_unknown_fields
from .media import validate_message_content

_ALLOWED_TOP_LEVEL = {
    "model",
    "input",
    "instructions",
    "max_output_tokens",
    "temperature",
    "top_p",
    "stream",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "reasoning",
    "text",
    "store",
}
_ALLOWED_MESSAGE_ROLES = {"user", "assistant", "system", "developer"}
_RESPONSE_STATUS = {"completed", "in_progress", "incomplete", "failed", "queued", "cancelled"}


def _error(param: str, message: str) -> ServiceError:
    return ServiceError("VALIDATION_ERROR", message, param=param)


def _require_non_empty_string(value: Any, param: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(param, f"{param} must be a non-empty string.")
    return value


def _responses_content_to_chat(content: Any, *, param: str) -> Any:
    """Responses message content를 기존 media validator가 이해하는 형태로 좁힌다.

    Responses는 이전 assistant output item을 그대로 다음 input에 넣을 수 있으므로
    input_text뿐 아니라 output_text/refusal도 텍스트로 취급한다. 변환 결과는
    validation에만 쓰고 실제 upstream payload는 원본 Responses item을 유지한다.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or not content:
        raise _error(param, f"{param} must be a string or a non-empty content array.")
    converted: list[dict[str, Any]] = []
    for index, part in enumerate(content):
        part_param = f"{param}[{index}]"
        if not isinstance(part, dict):
            raise _error(part_param, f"{part_param} must be an object.")
        part_type = part.get("type")
        if part_type in {"input_text", "output_text"}:
            allowed = {"type", "text"}
            if part_type == "output_text":
                allowed |= {"annotations", "logprobs"}
            reject_unknown_fields(part, allowed, part_param)
            text = part.get("text")
            if not isinstance(text, str):
                raise _error(f"{part_param}.text", f"{part_param}.text must be a string.")
            converted.append({"type": "text", "text": text})
            continue
        if part_type == "refusal":
            reject_unknown_fields(part, {"type", "refusal"}, part_param)
            refusal = part.get("refusal")
            if not isinstance(refusal, str):
                raise _error(f"{part_param}.refusal", f"{part_param}.refusal must be a string.")
            converted.append({"type": "text", "text": refusal})
            continue
        if part_type == "input_image":
            reject_unknown_fields(part, {"type", "image_url", "detail"}, part_param)
            image_url = _require_non_empty_string(part.get("image_url"), f"{part_param}.image_url")
            detail = part.get("detail", "auto")
            if detail not in {"auto", "low", "high"}:
                raise _error(f"{part_param}.detail", f"{part_param}.detail must be auto, low, or high.")
            converted.append({"type": "image_url", "image_url": {"url": image_url, "detail": detail}})
            continue
        raise _error(
            f"{part_param}.type",
            f"{part_param}.type must be input_text, input_image, output_text, or refusal.",
        )
    return converted


def _validate_reasoning_item(item: dict[str, Any], param: str) -> None:
    reject_unknown_fields(
        item,
        {"type", "id", "status", "summary", "content", "encrypted_content"},
        param,
    )
    if "id" in item:
        _require_non_empty_string(item["id"], f"{param}.id")
    if "status" in item and item["status"] not in {"in_progress", "completed", "incomplete"}:
        raise _error(f"{param}.status", f"{param}.status is invalid.")
    if "encrypted_content" in item and item["encrypted_content"] is not None:
        _require_non_empty_string(item["encrypted_content"], f"{param}.encrypted_content")
    for field, allowed_type in (("summary", "summary_text"), ("content", "reasoning_text")):
        if field not in item:
            continue
        parts = item[field]
        if not isinstance(parts, list):
            raise _error(f"{param}.{field}", f"{param}.{field} must be an array.")
        for index, part in enumerate(parts):
            part_param = f"{param}.{field}[{index}]"
            if not isinstance(part, dict):
                raise _error(part_param, f"{part_param} must be an object.")
            reject_unknown_fields(part, {"type", "text"}, part_param)
            if part.get("type") != allowed_type or not isinstance(part.get("text"), str):
                raise _error(part_param, f"{part_param} must be a {allowed_type} item.")


def _validate_input(
    value: Any,
    *,
    allowed_modalities: tuple[str, ...],
    max_image_inputs: int,
    allowed_image_url_schemes: tuple[str, ...],
    max_image_bytes: int,
    max_image_pixels: int,
    allowed_image_mime_types: tuple[str, ...],
) -> None:
    if isinstance(value, str):
        if not value:
            raise _error("input", "input must not be empty.")
        if "text" not in allowed_modalities:
            raise _error("input", "The active main model profile does not accept text input.")
        return
    if not isinstance(value, list) or not value:
        raise _error("input", "input must be a non-empty string or item array.")

    image_count = 0
    for index, item in enumerate(value):
        param = f"input[{index}]"
        if not isinstance(item, dict):
            raise _error(param, f"{param} must be an object.")
        item_type = item.get("type", "message")
        if item_type == "message":
            reject_unknown_fields(item, {"type", "role", "content", "status", "id"}, param)
            role = item.get("role")
            if role not in _ALLOWED_MESSAGE_ROLES:
                raise _error(f"{param}.role", f"{param}.role must be one of {sorted(_ALLOWED_MESSAGE_ROLES)}.")
            if "id" in item:
                _require_non_empty_string(item["id"], f"{param}.id")
            if "status" in item and item["status"] not in {"in_progress", "completed", "incomplete"}:
                raise _error(f"{param}.status", f"{param}.status is invalid.")
            chat_content = _responses_content_to_chat(item.get("content"), param=f"{param}.content")
            images, _audios, _videos = validate_message_content(
                chat_content,
                allowed_modalities=set(allowed_modalities),
                max_image_inputs=max_image_inputs,
                allowed_image_url_schemes=set(allowed_image_url_schemes),
                max_image_bytes=max_image_bytes,
                max_image_pixels=max_image_pixels,
                allowed_image_mime_types=set(allowed_image_mime_types),
            )
            image_count += images
            continue
        if item_type == "reasoning":
            _validate_reasoning_item(item, param)
            continue
        if item_type == "function_call":
            reject_unknown_fields(item, {"type", "id", "call_id", "name", "arguments", "status"}, param)
            _require_non_empty_string(item.get("call_id"), f"{param}.call_id")
            _require_non_empty_string(item.get("name"), f"{param}.name")
            _require_non_empty_string(item.get("arguments"), f"{param}.arguments")
            if "id" in item:
                _require_non_empty_string(item["id"], f"{param}.id")
            if "status" in item and item["status"] not in {"in_progress", "completed", "incomplete"}:
                raise _error(f"{param}.status", f"{param}.status is invalid.")
            continue
        if item_type == "function_call_output":
            reject_unknown_fields(item, {"type", "id", "call_id", "output", "status"}, param)
            _require_non_empty_string(item.get("call_id"), f"{param}.call_id")
            if not isinstance(item.get("output"), str):
                raise _error(f"{param}.output", f"{param}.output must be a string in this platform contract.")
            if "id" in item:
                _require_non_empty_string(item["id"], f"{param}.id")
            if "status" in item and item["status"] not in {"in_progress", "completed", "incomplete"}:
                raise _error(f"{param}.status", f"{param}.status is invalid.")
            continue
        raise _error(f"{param}.type", f"Unsupported Responses input item type: {item_type!r}.")

    if image_count > max_image_inputs:
        raise _error("input", f"Responses input contains {image_count} images; limit is {max_image_inputs}.")


def _validate_tools(payload: dict[str, Any], policy: dict[str, Any] | None) -> None:
    tools = payload.get("tools")
    tool_policy = _chat_policy(policy).get("tool_calling", {})
    if tools is None:
        if "tool_choice" in payload and payload["tool_choice"] != "none":
            raise _error("tool_choice", "tool_choice requires tools unless it is 'none'.")
        return
    if not isinstance(tool_policy, dict) or tool_policy.get("enabled") is not True:
        raise _error("tools", "Function tools are not enabled for the active main model profile.")
    max_tools = int(tool_policy.get("max_tools", 64))
    if not isinstance(tools, list) or not tools or len(tools) > max_tools:
        raise _error("tools", f"tools must be a non-empty array with at most {max_tools} entries.")
    names: set[str] = set()
    for index, tool in enumerate(tools):
        param = f"tools[{index}]"
        if not isinstance(tool, dict):
            raise _error(param, f"{param} must be an object.")
        reject_unknown_fields(tool, {"type", "name", "description", "parameters", "strict"}, param)
        if tool.get("type") != "function":
            raise _error(f"{param}.type", "Only function tools are supported by this platform Responses contract.")
        name = _require_non_empty_string(tool.get("name"), f"{param}.name")
        if name in names:
            raise _error(f"{param}.name", f"Duplicate tool name: {name}.")
        names.add(name)
        if "description" in tool and not isinstance(tool["description"], str):
            raise _error(f"{param}.description", f"{param}.description must be a string.")
        if "parameters" in tool and not isinstance(tool["parameters"], dict):
            raise _error(f"{param}.parameters", f"{param}.parameters must be an object.")
        if "strict" in tool and not isinstance(tool["strict"], bool):
            raise _error(f"{param}.strict", f"{param}.strict must be boolean.")

    choice = payload.get("tool_choice", "auto")
    if isinstance(choice, str):
        if choice not in {"auto", "none", "required"}:
            raise _error("tool_choice", "tool_choice must be auto, none, required, or a named function choice.")
    elif isinstance(choice, dict):
        reject_unknown_fields(choice, {"type", "name"}, "tool_choice")
        if choice.get("type") != "function":
            raise _error("tool_choice.type", "Named tool_choice.type must be function.")
        name = _require_non_empty_string(choice.get("name"), "tool_choice.name")
        if name not in names:
            raise _error("tool_choice.name", f"tool_choice names unknown function {name!r}.")
    else:
        raise _error("tool_choice", "tool_choice must be a string or object.")

    if "parallel_tool_calls" in payload:
        value = payload["parallel_tool_calls"]
        if not isinstance(value, bool):
            raise _error("parallel_tool_calls", "parallel_tool_calls must be boolean.")
        if value and tool_policy.get("allow_parallel_tool_calls") is not True:
            raise _error("parallel_tool_calls", "parallel_tool_calls=true is not enabled for this model.")


def _validate_text_config(payload: dict[str, Any], policy: dict[str, Any] | None) -> None:
    if "text" not in payload:
        return
    text = payload["text"]
    if not isinstance(text, dict):
        raise _error("text", "text must be an object when provided.")
    reject_unknown_fields(text, {"format"}, "text")
    if "format" not in text:
        return
    fmt = text["format"]
    if not isinstance(fmt, dict):
        raise _error("text.format", "text.format must be an object.")
    format_type = fmt.get("type")
    if format_type == "json_schema":
        response_format = {
            "type": "json_schema",
            "json_schema": {
                key: value
                for key, value in fmt.items()
                if key in {"name", "description", "strict", "schema"}
            },
        }
        reject_unknown_fields(fmt, {"type", "name", "description", "strict", "schema"}, "text.format")
    else:
        reject_unknown_fields(fmt, {"type"}, "text.format")
        response_format = {"type": format_type}
    shadow = {"response_format": response_format, "messages": [{"role": "user", "content": "JSON"}]}
    _validate_response_format(response_format, shadow, policy)


def _validate_reasoning(payload: dict[str, Any], policy: dict[str, Any] | None) -> None:
    if "reasoning" not in payload:
        return
    value = payload["reasoning"]
    if not isinstance(value, dict):
        raise _error("reasoning", "reasoning must be an object.")
    reject_unknown_fields(value, {"effort"}, "reasoning")
    effort = value.get("effort")
    reasoning_policy = _chat_policy(policy).get("reasoning", {})
    if not isinstance(reasoning_policy, dict) or reasoning_policy.get("enabled") is not True:
        raise _error("reasoning", "reasoning is not enabled for the active main model profile.")
    enabled_effort = reasoning_policy.get("responses_effort")
    allowed = {"none"}
    if isinstance(enabled_effort, str) and enabled_effort:
        allowed.add(enabled_effort)
    if effort not in allowed:
        raise _error("reasoning.effort", f"reasoning.effort must be one of {sorted(allowed)} for the active profile.")


def validate_responses_request(
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
    request = ensure_request_object(payload)
    unknown = sorted(set(request) - _ALLOWED_TOP_LEVEL)
    if unknown:
        raise _error(unknown[0], f"Unsupported Responses field(s): {', '.join(unknown)}.")
    if request.get("model") != expected_model:
        raise _error("model", f"model must be {expected_model}.")
    if "input" not in request:
        raise _error("input", "input is required.")
    _validate_input(
        request["input"],
        allowed_modalities=allowed_input_modalities,
        max_image_inputs=max_image_inputs,
        allowed_image_url_schemes=allowed_image_url_schemes,
        max_image_bytes=max_image_bytes,
        max_image_pixels=max_image_pixels,
        allowed_image_mime_types=allowed_image_mime_types,
    )
    if "instructions" in request and not isinstance(request["instructions"], str):
        raise _error("instructions", "instructions must be a string.")
    if "max_output_tokens" in request:
        value = request["max_output_tokens"]
        if not is_int(value) or value < 1:
            raise _error("max_output_tokens", "max_output_tokens must be an integer greater than or equal to 1.")
        if max_output_tokens is not None and value > max_output_tokens:
            raise _error("max_output_tokens", f"max_output_tokens must be less than or equal to {max_output_tokens}.")
    if "temperature" in request and (not is_number(request["temperature"]) or request["temperature"] < 0 or request["temperature"] > 2):
        raise _error("temperature", "temperature must be a number between 0 and 2.")
    if "top_p" in request and (not is_number(request["top_p"]) or request["top_p"] <= 0 or request["top_p"] > 1):
        raise _error("top_p", "top_p must be a number in the interval (0, 1].")
    if "stream" in request and not isinstance(request["stream"], bool):
        raise _error("stream", "stream must be boolean.")
    if "store" in request and request["store"] is not False:
        raise _error("store", "This platform is stateless; store may only be false when provided.")
    _validate_tools(request, request_parameter_policy)
    _validate_text_config(request, request_parameter_policy)
    _validate_reasoning(request, request_parameter_policy)
    return dict(request)


def normalize_responses_request_for_runtime(
    payload: dict[str, Any],
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    upstream = dict(payload)
    # vLLM의 Responses store는 별도 in-memory feature이며 플랫폼 durable state가 아니다.
    # Gateway는 stateless contract를 소유하므로 항상 false를 명시한다.
    upstream["store"] = False
    tool_policy = _chat_policy(policy).get("tool_calling", {})
    if upstream.get("tools") and isinstance(tool_policy, dict) and tool_policy.get("allow_parallel_tool_calls") is not True:
        upstream.setdefault("parallel_tool_calls", False)
    return upstream


def _project_content_part(part: Any) -> dict[str, Any] | None:
    if not isinstance(part, dict):
        return None
    part_type = part.get("type")
    fields_by_type = {
        "output_text": {"type", "text", "annotations", "logprobs"},
        "refusal": {"type", "refusal"},
        "reasoning_text": {"type", "text"},
        "summary_text": {"type", "text"},
    }
    allowed = fields_by_type.get(part_type)
    if allowed is None:
        return None
    return {key: value for key, value in part.items() if key in allowed}


def _project_output_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    fields_by_type = {
        "message": {"id", "type", "role", "status", "content"},
        "function_call": {"id", "type", "call_id", "name", "arguments", "status"},
        "reasoning": {"id", "type", "status", "summary", "content", "encrypted_content"},
    }
    allowed = fields_by_type.get(item_type)
    if allowed is None:
        return None
    projected = {key: value for key, value in item.items() if key in allowed}
    if isinstance(projected.get("content"), list):
        projected["content"] = [p for raw in projected["content"] if (p := _project_content_part(raw)) is not None]
    if isinstance(projected.get("summary"), list):
        projected["summary"] = [p for raw in projected["summary"] if (p := _project_content_part(raw)) is not None]
    return projected


def project_responses_response(response: Any, *, expected_model: str) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", "Responses upstream returned a non-object response.")
    if response.get("object") != "response":
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", "Responses upstream object must be 'response'.")
    if response.get("model") != expected_model:
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", "Responses upstream model does not match the requested logical model.")
    status = response.get("status")
    if status not in _RESPONSE_STATUS:
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", "Responses upstream status is invalid.")
    output = response.get("output")
    if not isinstance(output, list):
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", "Responses upstream output must be an array.")
    projected_output = [p for raw in output if (p := _project_output_item(raw)) is not None]
    if len(projected_output) != len(output):
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", "Responses upstream emitted an unsupported output item type.")
    allowed = {
        "id", "created_at", "error", "incomplete_details", "instructions", "metadata", "model", "object",
        "output", "parallel_tool_calls", "temperature", "tool_choice", "tools", "top_p", "background",
        "max_output_tokens", "max_tool_calls", "previous_response_id", "reasoning", "service_tier", "status",
        "store", "text", "top_logprobs", "truncation", "usage",
    }
    projected = {key: value for key, value in response.items() if key in allowed}
    projected["output"] = projected_output
    return projected


def project_responses_stream_event(event: Any, *, expected_model: str) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", "Responses stream event must be an object.")
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type.startswith("response."):
        raise ServiceError("UPSTREAM_RESPONSE_INVALID", "Responses stream event type is invalid.")
    allowed_common = {
        "type", "sequence_number", "output_index", "content_index", "item_id", "delta", "text",
        "arguments", "part", "item", "response", "logprobs", "obfuscation",
    }
    projected = {key: value for key, value in event.items() if key in allowed_common}
    if isinstance(projected.get("response"), dict):
        projected["response"] = project_responses_response(projected["response"], expected_model=expected_model)
    if isinstance(projected.get("item"), dict):
        item = _project_output_item(projected["item"])
        if item is None:
            raise ServiceError("UPSTREAM_RESPONSE_INVALID", "Responses stream emitted an unsupported output item type.")
        projected["item"] = item
    if isinstance(projected.get("part"), dict):
        part = _project_content_part(projected["part"])
        if part is None:
            raise ServiceError("UPSTREAM_RESPONSE_INVALID", "Responses stream emitted an unsupported content part type.")
        projected["part"] = part
    return projected
