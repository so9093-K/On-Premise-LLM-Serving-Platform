from __future__ import annotations

from .pii import mask_pii
from .secret import mask_secrets

# 순서대로 적용된다. 새 마스킹 규칙은 함수를 만들어 이 목록에 추가하기만 하면 된다.
_MASKERS = (mask_pii, mask_secrets)


def mask_sensitive_text(text: str) -> str:
    """PII/secret 탐지 규칙을 순서대로 적용해 마스킹된 텍스트를 반환한다."""
    for masker in _MASKERS:
        text = masker(text)
    return text
