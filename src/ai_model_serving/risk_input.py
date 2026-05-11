from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .risk import assessment_response, system_signal


@dataclass(frozen=True)
class RiskInputPolicy:
    """Detector input guard used before calling risk vLLM runtimes.

    The public request contract still accepts a larger JSON body/prompt limit.
    This policy protects the smaller detector context window and reports an
    explicit signal-only ``TRUNCATED_INPUT`` result instead of sending an input
    that the detector runtime may silently truncate or reject.
    """

    max_prompt_chars: int
    source: str = "risk_input_policy"

    def overflowed(self, prompt: str) -> bool:
        return len(prompt) > self.max_prompt_chars

    def system_signal_response(self, *, detector_name: str, source_model: str) -> dict[str, Any]:
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
