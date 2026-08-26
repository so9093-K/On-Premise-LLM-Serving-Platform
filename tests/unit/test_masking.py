"""mask_sensitive_text가 등록된 masker를 빠짐없이 적용하는지 검증한다.

각 masker 자체의 동작(치환 정확성, 깨끗한 텍스트 보존)은 test_pii_protection_detector
/ test_secret_exposure_detector가 이미 다룬다. 여기서 고정하는 것은 합성뿐이다 --
_MASKERS에서 하나가 빠지면 그 종류의 민감정보가 그대로 로그로 나간다."""

from __future__ import annotations

from ai_model_serving.detectors.masking import mask_sensitive_text


def test_masks_both_pii_and_secret_in_one_pass():
    text = "이메일 hong@example.com, 키 sk-ant-api03-abcdefghijklmnopqrst"
    masked = mask_sensitive_text(text)
    assert "hong@example.com" not in masked
    assert "sk-ant-api03-abcdefghijklmnopqrst" not in masked
    assert "[EMAIL_ADDRESS]" in masked
    assert "[ANTHROPIC_API_KEY]" in masked

