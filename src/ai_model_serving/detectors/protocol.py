from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RiskDetector(Protocol):
    """in-process local risk detector가 구현해야 하는 protocol이다.

    Implementors return a complete assessment_response dict directly, without
    going through a vLLM runtime. The response must satisfy the same
    signal-only contract as vLLM-based detectors.
    """

    async def assess(self, text: str) -> dict[str, Any]: ...


class DetectionProfile(Enum):
    """같은 탐지 규칙을 두 소비자가 서로 다른 오류 선호로 쓰기 위한 축이다.

    로그 마스킹과 risk signal은 파이프라인을 공유하지만 틀렸을 때의 손해가
    정반대다. 마스킹이 놓치면 평문 PII가 로그에 영구히 남고, 신호가 과하면
    소비자가 신호 전체를 무시한다. 임계값이 하나뿐이던 동안에는 어느 쪽으로
    옮겨도 반대쪽이 깨졌다.

    현재 두 프로파일이 갈라지는 지점은 **검증에 실패한 span의 처리**뿐이다.
    SIGNAL은 버리고, MASKING은 신뢰도 0으로 남겨 가린다. 엔트로피 임계값은
    측정 결과 재현율 조절 손잡이가 아니어서(내리면 늘어나는 건 파일 경로와
    테스트 이름뿐이었다) 프로파일로 가르지 않는다.
    """

    SIGNAL = "signal"
    MASKING = "masking"
