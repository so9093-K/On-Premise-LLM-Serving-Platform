"""PII Protection detector 단위 테스트.

결정적(deterministic) local recognizer와 entity->D-code 매핑을 다룬다.

검증하는 불변식:
- 출력 category의 D1~D5 스키마 호환성
- data_exposure family validator 통과
- span_count가 탐지 개수를 나타냄
- 원본 PII 값이 response에 없음
- boolean 일관성(risk_detected == model_risk_detected == 탐지된 category 존재 여부)
"""
from __future__ import annotations

import asyncio

import pytest

from ai_model_serving.detectors.pii import PIIProtectionDetector, mask_pii


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
        ("여권번호: MA1234567", "KR_PASSPORT", "D1"),
        ("면허번호: 12-34-567890-12", "KR_DRIVER_LICENSE", "D1"),
        ("이메일: hong@example.com", "EMAIL_ADDRESS", "D2"),
        # 휴대폰/서울/지역/구형 prefix는 정규식 분기가 서로 다르다.
        ("연락처: 010-1234-5678", "PHONE_NUMBER", "D2"),
        ("사무실: 02-1234-5678", "PHONE_NUMBER", "D2"),
        ("경기 번호: 031-123-4567", "PHONE_NUMBER", "D2"),
        ("구형 번호: 011-123-4567", "PHONE_NUMBER", "D2"),
        ("서버 IP 192.168.1.100에 접속하세요.", "IP_ADDRESS", "D5"),
    ],
)
def test_entity_is_reported_with_expected_code(text, label, code):
    response = asyncio.run(PIIProtectionDetector().assess(text))
    detected = {c["label"]: c for c in response["categories"] if c["detected"]}
    assert label in detected
    assert detected[label]["code"] == code
    assert detected[label]["family"] == "data_exposure"
    assert detected[label]["span_count"] >= 1


# ---------------------------------------------------------------------------
# Detector 통합 테스트
# ---------------------------------------------------------------------------

class TestPIIProtectionDetector:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_rrn_in_text_returns_d1_signal(self):
        detector = PIIProtectionDetector()
        response = self._run(detector.assess("주민번호: 901201-1234567"))
        assert response["risk_detected"] is True
        assert response["model_risk_detected"] is True
        d1_cats = [c for c in response["categories"] if c.get("code") == "D1"]
        assert d1_cats
        assert d1_cats[0]["span_count"] >= 1

    def test_clean_text_returns_safe_response(self):
        detector = PIIProtectionDetector()
        response = self._run(detector.assess("오늘 날씨가 맑습니다."))
        assert response["risk_detected"] is False
        assert response["model_risk_detected"] is False
        assert response["status"] == "completed"
        assert response["assessment_complete"] is True
        safe_cats = [c for c in response["categories"] if c.get("code") is None]
        assert safe_cats

    def test_boolean_consistency_when_detected(self):
        detector = PIIProtectionDetector()
        response = self._run(detector.assess("이메일: test@example.com이고 주민번호: 901201-1234567"))
        detected_count = sum(1 for c in response["categories"] if c["detected"])
        assert response["risk_detected"] == (detected_count > 0)
        assert response["model_risk_detected"] == response["risk_detected"]
        assert response["attention_required"] == (response["model_risk_detected"] or response["system_signal_detected"])

    def test_response_contains_no_raw_pii_values(self):
        rrn = "901201-1234567"
        email = "test@example.com"
        detector = PIIProtectionDetector()
        response = self._run(detector.assess(f"주민번호: {rrn}, 이메일: {email}"))
        import json
        response_str = json.dumps(response)
        assert rrn not in response_str
        assert email not in response_str

    def test_span_count_reflects_multiple_detections(self):
        detector = PIIProtectionDetector()
        # 서로 다른 주민번호 2개
        response = self._run(detector.assess("첫번째 901201-1234567 두번째 820315-2345678"))
        d1_cats = [c for c in response["categories"] if c.get("code") == "D1"]
        assert len(d1_cats) == 1
        assert d1_cats[0]["span_count"] == 2

    def test_source_model_field_present_and_non_empty(self):
        detector = PIIProtectionDetector()
        response = self._run(detector.assess("이메일: test@example.com"))
        for cat in response["categories"]:
            assert isinstance(cat.get("source_model"), str)
            assert cat["source_model"]

    def test_korean_phone_number_returns_d2_signal(self):
        detector = PIIProtectionDetector()
        response = self._run(detector.assess("내 전화번호는 010-3817-5168입니다."))
        assert response["risk_detected"] is True
        d2_cats = [c for c in response["categories"] if c.get("code") == "D2"]
        assert d2_cats
        assert d2_cats[0]["label"] == "PHONE_NUMBER"
        assert d2_cats[0]["span_count"] >= 1

    def test_ip_address_is_d5_not_d2_phone(self):
        detector = PIIProtectionDetector()
        response = self._run(detector.assess("서버 IP 192.168.1.100에 접속하세요."))
        detected = {
            category["label"]: category
            for category in response["categories"]
            if category["detected"]
        }
        assert "PHONE_NUMBER" not in detected
        assert "IP_ADDRESS" in detected
        assert detected["IP_ADDRESS"]["code"] == "D5"

    def test_email_domain_is_not_reported_as_url(self):
        detector = PIIProtectionDetector()
        response = self._run(
            detector.assess(
                "담당자 이메일은 hong@example.com이고 연락처는 010-1234-5678입니다."
            )
        )
        detected = {
            category["label"]: category
            for category in response["categories"]
            if category["detected"]
        }
        assert set(detected) == {"EMAIL_ADDRESS", "PHONE_NUMBER"}
        assert detected["EMAIL_ADDRESS"]["span_count"] == 1

    def test_forbidden_fields_not_in_response(self):
        detector = PIIProtectionDetector()
        response = self._run(detector.assess("주민번호: 901201-1234567"))
        forbidden = {"allow", "block", "decision", "action", "safe_to_send", "policy_overrides"}
        assert not forbidden.intersection(response)


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
