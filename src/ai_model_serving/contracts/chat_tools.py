from __future__ import annotations

from typing import Any

from ..errors import ServiceError
from .common import reject_unknown_fields


def _validation_error(param: str, message: str) -> ServiceError:
    return ServiceError("VALIDATION_ERROR", message, param=param)


def _validate_tools(tools: Any, *, max_tools: int = 64) -> None:
    if not isinstance(tools, list) or not tools:
        raise _validation_error("tools", "tools must be a non-empty array when provided.")
    if len(tools) > max_tools:
        raise _validation_error("tools", f"tools must contain {max_tools} items or fewer.")
    names: set[str] = set()
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise _validation_error(f"tools[{index}]", f"tools[{index}] must be a function tool object.")
        reject_unknown_fields(tool, {"type", "function"}, f"tools[{index}]")
        function = tool.get("function")
        if not isinstance(function, dict):
            raise _validation_error(f"tools[{index}].function", f"tools[{index}].function must be an object.")
        reject_unknown_fields(function, {"name", "description", "parameters", "strict"}, f"tools[{index}].function")
        name = function.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            raise _validation_error(f"tools[{index}].function.name", f"tools[{index}].function.name must be a non-empty string of 64 chars or fewer.")
        if name in names:
            raise _validation_error(f"tools[{index}].function.name", f"duplicate tool name is not allowed: {name}.")
        names.add(name)
        if "description" in function and not isinstance(function["description"], str):
            raise _validation_error(f"tools[{index}].function.description", f"tools[{index}].function.description must be a string.")
        if "parameters" in function and not isinstance(function["parameters"], dict):
            raise _validation_error(f"tools[{index}].function.parameters", f"tools[{index}].function.parameters must be a JSON Schema object.")
        if "strict" in function and not isinstance(function["strict"], bool):
            raise _validation_error(f"tools[{index}].function.strict", f"tools[{index}].function.strict must be boolean when provided.")


def _validate_tool_choice(value: Any) -> None:
    if isinstance(value, str):
        if value in {"auto", "none", "required"}:
            return
        raise _validation_error("tool_choice", "tool_choice must be auto, none, required, or a function choice object.")
    if not isinstance(value, dict) or value.get("type") != "function":
        raise _validation_error("tool_choice", "tool_choice must be auto, none, required, or a function choice object.")
    reject_unknown_fields(value, {"type", "function"}, "tool_choice")
    function = value.get("function")
    if not isinstance(function, dict):
        raise _validation_error("tool_choice.function", "tool_choice.function must be an object.")
    reject_unknown_fields(function, {"name"}, "tool_choice.function")
    if not isinstance(function.get("name"), str) or not function["name"].strip():
        raise _validation_error("tool_choice.function.name", "tool_choice.function.name must be a non-empty string.")


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
        raise _validation_error("tool_choice", "tool_choice requires a non-empty tools array unless it is 'none'.")
    if isinstance(choice, dict):
        name = choice.get("function", {}).get("name") if isinstance(choice.get("function"), dict) else None
        tool_names = _tool_names(tools)
        if isinstance(name, str) and name not in tool_names:
            allowed = ", ".join(sorted(tool_names)) or "none"
            raise _validation_error("tool_choice.function.name", f"tool_choice.function.name is {name}; use one of the provided tools: {allowed}.")


def _validate_tool_calls(tool_calls: Any) -> None:
    if not isinstance(tool_calls, list) or not tool_calls:
        raise _validation_error("tool_calls", "tool_calls must be a non-empty array when provided.")
    for index, call in enumerate(tool_calls):
        if not isinstance(call, dict):
            raise _validation_error(f"tool_calls[{index}]", f"tool_calls[{index}] must be an object.")
        reject_unknown_fields(call, {"id", "type", "function"}, f"tool_calls[{index}]")
        if not isinstance(call.get("id"), str) or not call["id"].strip():
            raise _validation_error(f"tool_calls[{index}].id", f"tool_calls[{index}].id must be a non-empty string.")
        if call.get("type") != "function":
            raise _validation_error(f"tool_calls[{index}].type", f"tool_calls[{index}].type must be function.")
        function = call.get("function")
        if not isinstance(function, dict):
            raise _validation_error(f"tool_calls[{index}].function", f"tool_calls[{index}].function must be an object.")
        reject_unknown_fields(function, {"name", "arguments"}, f"tool_calls[{index}].function")
        if not isinstance(function.get("name"), str) or not function["name"].strip():
            raise _validation_error(f"tool_calls[{index}].function.name", f"tool_calls[{index}].function.name must be a non-empty string.")
        if not isinstance(function.get("arguments"), str):
            raise _validation_error(f"tool_calls[{index}].function.arguments", f"tool_calls[{index}].function.arguments must be a string.")
