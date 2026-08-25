from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .risk import assessment_response, system_signal


# detector runtime의 context window에서 안전한 프롬프트 상한(문자 수)을 구한다.
# 64는 chat template과 system 지시가 차지하는 토큰 여유이고, 4는 토큰당 문자 수
# 추정치다. settings(런타임 적용)와 governance 검증(설정 상한 확인)이 같은 값을
#써야 하므로 여기 한 곳에만 둔다.
DETECTOR_PROMPT_TOKEN_HEADROOM = 64
DETECTOR_CHARS_PER_TOKEN = 4


def detector_prompt_char_budget(max_model_len: int) -> int:
    return max(1, (int(max_model_len) - DETECTOR_PROMPT_TOKEN_HEADROOM) * DETECTOR_CHARS_PER_TOKEN)


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
