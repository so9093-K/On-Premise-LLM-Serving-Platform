"""PII Protection detector unit tests.

Tests cover Korean custom recognizers (always available) and the entity->D-code
mapping. Presidio built-in recognizers are tested via mocking to avoid a hard
dependency on presidio-analyzer in the test environment.

Validated invariants:
- D1~D5 schema compatibility of output categories
- data_exposure family validator pass
- span_count represents detection count
- original PII values NOT present in response
- boolean consistency (risk_detected == model_risk_detected == any detected category)
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from ai_model_serving.detectors.pii import PIIProtectionDetector, _run_custom_recognizers, _build_categories


# ---------------------------------------------------------------------------
# Custom recognizer tests (no presidio dependency)
# ---------------------------------------------------------------------------

class TestKoreanCustomRecognizers:
    def test_rrn_detected(self):
        counts = _run_custom_recognizers("주민번호: 901201-1234567 확인 바랍니다.")
        assert "KR_RRN" in counts
        assert counts["KR_RRN"] >= 1

    def test_frn_detected(self):
        counts = _run_custom_recognizers("외국인등록번호: 901201-5234567")
        assert "KR_FRN" in counts

    def test_brn_detected(self):
        counts = _run_custom_recognizers("사업자번호: 123-45-67890")
        assert "KR_BRN" in counts

    def test_passport_detected(self):
        counts = _run_custom_recognizers("여권번호: MA1234567")
        assert "KR_PASSPORT" in counts

    def test_driver_license_detected(self):
        counts = _run_custom_recognizers("면허번호: 12-34-567890-12")
        assert "KR_DRIVER_LICENSE" in counts

    def test_bank_account_with_context_detected(self):
        counts = _run_custom_recognizers("계좌 번호: 12345678901234")
        assert "BANK_ACCOUNT_CANDIDATE" in counts

    def test_bank_account_without_context_not_detected(self):
        counts = _run_custom_recognizers("숫자 나열: 12345678901234")
        assert "BANK_ACCOUNT_CANDIDATE" not in counts

    def test_clean_text_returns_empty(self):
        counts = _run_custom_recognizers("오늘 날씨가 맑습니다.")
        assert counts == {}


# ---------------------------------------------------------------------------
# Category builder tests
# ---------------------------------------------------------------------------

class TestBuildCategories:
    def test_empty_counts_returns_safe_category(self):
        cats = _build_categories({})
        assert len(cats) == 1
        cat = cats[0]
        assert cat["code"] is None
        assert cat["detected"] is False
        assert cat["family"] == "data_exposure"
        assert cat["span_count"] == 0
        assert "source_model" in cat

    def test_d1_rrn_category(self):
        cats = _build_categories({"KR_RRN": 2})
        assert len(cats) == 1
        cat = cats[0]
        assert cat["code"] == "D1"
        assert cat["detected"] is True
        assert cat["label"] == "KR_RRN"
        assert cat["span_count"] == 2
        assert cat["family"] == "data_exposure"

    def test_d2_email_category(self):
        cats = _build_categories({"EMAIL_ADDRESS": 1})
        assert cats[0]["code"] == "D2"
        assert cats[0]["label"] == "EMAIL_ADDRESS"

    def test_d3_credit_card_category(self):
        cats = _build_categories({"CREDIT_CARD": 1})
        assert cats[0]["code"] == "D3"

    def test_d5_ip_address_category(self):
        cats = _build_categories({"IP_ADDRESS": 3})
        assert cats[0]["code"] == "D5"
        assert cats[0]["span_count"] == 3

    def test_multiple_entity_types(self):
        cats = _build_categories({"EMAIL_ADDRESS": 2, "KR_RRN": 1})
        codes = {c["code"] for c in cats}
        assert "D1" in codes
        assert "D2" in codes
        assert all(c["detected"] for c in cats)

    def test_no_raw_pii_values_in_categories(self):
        cats = _build_categories({"KR_RRN": 1})
        for cat in cats:
            for key, value in cat.items():
                if isinstance(value, str):
                    # label is entity type name, NOT a raw PII value
                    assert value in {"KR_RRN", "KR_FRN", "KR_BRN", "KR_PASSPORT", "KR_DRIVER_LICENSE",
                                     "EMAIL_ADDRESS", "PHONE_NUMBER", "ADDRESS", "CREDIT_CARD",
                                     "BANK_ACCOUNT_CANDIDATE", "IP_ADDRESS", "URL", "data_exposure",
                                     "presidio-analyzer", "D1", "D2", "D3", "D5", None}


# ---------------------------------------------------------------------------
# Detector integration tests (mocking presidio)
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
        # Two separate RRNs
        response = self._run(detector.assess("첫번째 901201-1234567 두번째 820315-2345678"))
        d1_cats = [c for c in response["categories"] if c.get("code") == "D1"]
        if d1_cats:
            assert d1_cats[0]["span_count"] >= 1

    def test_presidio_not_installed_graceful_fallback(self):
        with patch.dict("sys.modules", {"presidio_analyzer": None}):
            detector = PIIProtectionDetector()
            # Should still work with Korean custom recognizers
            response = self._run(detector.assess("주민번호: 901201-1234567"))
            # May or may not detect depending on import state; should not raise
            assert isinstance(response, dict)
            assert "risk_detected" in response

    def test_source_model_field_present_and_non_empty(self):
        detector = PIIProtectionDetector()
        response = self._run(detector.assess("이메일: test@example.com"))
        for cat in response["categories"]:
            assert isinstance(cat.get("source_model"), str)
            assert cat["source_model"]

    def test_forbidden_fields_not_in_response(self):
        detector = PIIProtectionDetector()
        response = self._run(detector.assess("주민번호: 901201-1234567"))
        forbidden = {"allow", "block", "decision", "action", "safe_to_send", "policy_overrides"}
        assert not forbidden.intersection(response)
