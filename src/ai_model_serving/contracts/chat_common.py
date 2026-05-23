from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CHAT_ROLES = {"system", "user", "assistant"}
TOOL_CHAT_ROLES = {"system", "user", "assistant", "tool"}
UNSUPPORTED_CHAT_FIELDS = {"tools", "tool_choice", "parallel_tool_calls"}
UNSUPPORTED_MESSAGE_FIELDS = {"tool_calls", "tool_call_id"}


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
