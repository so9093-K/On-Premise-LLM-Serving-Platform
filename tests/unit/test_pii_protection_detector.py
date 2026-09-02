"""PII Protection detector 단위 테스트.

결정적(deterministic) local recognizer와 entity->D-code 매핑을 다룬다.

응답의 "모양"(필수 필드, boolean 일관성, 금지 필드, source_model, assessment_id
접두사)은 여기서 손으로 확인하지 않는다 -- 실제 detector 출력을
``validate_risk_response()``에 그대로 통과시킨다. 그게 Gateway가 런타임에
거는 바로 그 게이트이고, 손으로 쓴 부분 재구현보다 넓고 정확하다.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from ai_model_serving.contracts.risk import validate_risk_response
from ai_model_serving.detectors.pii import PIIProtectionDetector, mask_pii


def assess(text: str) -> dict:
    return asyncio.run(PIIProtectionDetector().assess(text))


# ---------------------------------------------------------------------------
# Entity -> D-code 매핑
# ---------------------------------------------------------------------------

# recognizer와 category builder를 각각 private helper로 직접 부르지 않고 공개
# assess() 하나로 확인한다. 두 단계를 따로 검증하면 "패턴은 잡히는데 code가 안
# 붙는" 조합은 오히려 아무도 안 보게 되고, 실제 계약은 assess() 응답이다.
@pytest.mark.parametrize(
    ("text", "label", "code"),
    [
        ("주민번호: 901201-1234567", "KR_RRN", "D1"),
        ("외국인등록번호: 901201-5234567", "KR_FRN", "D1"),
        # 실제 여권번호는 구형 `M`+숫자8, 신형 `M`+숫자3+영문1+숫자4다.
        # 예전 패턴이 잡던 `MA1234567`(영문 두 자 뒤 숫자 7)은 존재하지 않는
        # 형식이었고, 그래서 실제 여권번호를 하나도 탐지하지 못했다.
        ("여권번호: M12345678", "KR_PASSPORT", "D1"),
        ("여권번호: M123A4567", "KR_PASSPORT", "D1"),
        ("면허번호: 12-34-567890-12", "KR_DRIVER_LICENSE", "D1"),
        ("이메일: hong@example.com", "EMAIL_ADDRESS", "D2"),
        ("연락처: 010-1234-5678", "PHONE_NUMBER", "D2"),
        ("서버 IP 192.168.1.100에 접속하세요.", "IP_ADDRESS", "D5"),
    ],
)
def test_entity_is_reported_with_expected_code(text, label, code):
    response = assess(text)
    detected = {c["label"]: c for c in response["categories"] if c["detected"]}
    assert label in detected
    assert detected[label]["code"] == code
    assert detected[label]["family"] == "data_exposure"
    assert detected[label]["span_count"] >= 1


# ---------------------------------------------------------------------------
# 공개 응답 계약
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "이메일: test@example.com이고 주민번호: 901201-1234567",  # 여러 건 탐지
        "오늘 날씨가 맑습니다.",  # 탐지 없음
    ],
)
def test_detector_output_satisfies_public_risk_contract(text):
    # Gateway가 런타임에 거는 게이트를 그대로 통과시킨다: 필수 필드, 금지 필드,
    # assessment_id 접두사, category 모양, risk/model_risk/attention boolean 일관성,
    # assessment_complete 일관성, data_exposure의 source_model 필수 여부까지 한 번에.
    validate_risk_response(assess(text))


class TestPIIProtectionDetector:
    def test_clean_text_returns_safe_response(self):
        response = assess("오늘 날씨가 맑습니다.")
        assert response["risk_detected"] is False
        assert response["status"] == "completed"
        # 탐지가 없어도 safe category를 남겨야 한다 -- 빈 categories는 "검사했는데
        # 깨끗함"과 "아무것도 안 함"을 구분하지 못한다.
        assert [c for c in response["categories"] if c.get("code") is None]

    def test_response_contains_no_raw_pii_values(self):
        rrn = "901201-1234567"
        email = "test@example.com"
        response_str = json.dumps(assess(f"주민번호: {rrn}, 이메일: {email}"))
        assert rrn not in response_str
        assert email not in response_str

    def test_span_count_reflects_multiple_detections(self):
        # 서로 다른 주민번호 2개
        response = assess("첫번째 901201-1234567 두번째 820315-2345678")
        d1_cats = [c for c in response["categories"] if c.get("code") == "D1"]
        assert len(d1_cats) == 1
        assert d1_cats[0]["span_count"] == 2

    def test_ip_address_is_d5_not_d2_phone(self):
        response = assess("서버 IP 192.168.1.100에 접속하세요.")
        detected = {c["label"]: c for c in response["categories"] if c["detected"]}
        assert "PHONE_NUMBER" not in detected
        assert "IP_ADDRESS" in detected
        assert detected["IP_ADDRESS"]["code"] == "D5"

    def test_email_domain_is_not_reported_as_url(self):
        response = assess("담당자 이메일은 hong@example.com이고 연락처는 010-1234-5678입니다.")
        detected = {c["label"]: c for c in response["categories"] if c["detected"]}
        assert set(detected) == {"EMAIL_ADDRESS", "PHONE_NUMBER"}
        assert detected["EMAIL_ADDRESS"]["span_count"] == 1


class TestMaskPii:
    def test_replaces_detected_span_with_entity_label(self):
        masked = mask_pii("이메일은 hong@example.com입니다")
        assert "hong@example.com" not in masked
        assert "[EMAIL_ADDRESS]" in masked

    def test_multiple_spans_masked_without_offset_corruption(self):
        masked = mask_pii("이메일 hong@example.com 전화 010-1234-5678")
        assert "hong@example.com" not in masked
        assert "010-1234-5678" not in masked
        assert "[EMAIL_ADDRESS]" in masked
        assert "[PHONE_NUMBER]" in masked

    def test_text_without_pii_is_unchanged(self):
        text = "오늘 날씨가 좋습니다"
        assert mask_pii(text) == text
