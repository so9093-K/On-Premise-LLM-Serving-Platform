from __future__ import annotations

from typing import Any

from ..errors import ServiceError
from .common import reject_unknown_fields

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
