from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# OpenAI가 `system`을 대체한 역할이 `developer`다. 두 이름을 모두 받는다 --
# 실서빙 중인 chat template이 둘을 동일하게 처리하는 것을 확인했고, 표준 클라이언트가
# `developer`를 보내면 거부하는 쪽이 오히려 계약 위반이다.
CHAT_ROLES = {"system", "developer", "user", "assistant"}
TOOL_CHAT_ROLES = CHAT_ROLES | {"tool"}
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
