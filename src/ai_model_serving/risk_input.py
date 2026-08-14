from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .risk import assessment_response, system_signal


@dataclass(frozen=True)
class RiskInputPolicy:
    """risk vLLM runtime 호출 전에 적용하는 detector 입력 guard다.

    The public request contract still accepts a larger JSON body/prompt limit.
    This policy protects the smaller detector context window and reports an
    explicit signal-only ``TRUNCATED_INPUT`` result instead of sending an input
    that the detector runtime may silently truncate or reject.
    """

    max_prompt_chars: int

    def overflowed(self, prompt: str) -> bool:
        return len(prompt) > self.max_prompt_chars

    def system_signal_response(self, *, source_model: str) -> dict[str, Any]:
        return assessment_response(
            categories=[],
            system_signals=[
                system_signal(
                    "TRUNCATED_INPUT",
                    f"Risk input exceeded detector context guard ({self.max_prompt_chars} characters).",
                    source_model,
                    retryable=False,
                )
            ],
            status="failed",
            message="Risk input exceeded detector context guard before inference.",
        )
