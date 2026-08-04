"""mask_sensitive_text가 PII와 secret을 한 번에 마스킹하는지, 민감정보 없는
텍스트는 그대로 두는지 검증한다."""

from __future__ import annotations

from ai_model_serving.detectors.masking import mask_sensitive_text


def test_masks_both_pii_and_secret_in_one_pass():
    text = "이메일 hong@example.com, 키 sk-ant-api03-abcdefghijklmnopqrst"
    masked = mask_sensitive_text(text)
    assert "hong@example.com" not in masked
    assert "sk-ant-api03-abcdefghijklmnopqrst" not in masked
    assert "[EMAIL_ADDRESS]" in masked
    assert "[ANTHROPIC_API_KEY]" in masked


def test_text_without_sensitive_data_is_unchanged():
    text = "오늘 회의는 3시입니다"
    assert mask_sensitive_text(text) == text
