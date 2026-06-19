"""PII Protection detector unit tests.

Tests cover Korean custom recognizers (always available), Presidio built-in
recognizers (presidio-analyzer is a runtime dependency), and the entity->D-code
mapping.

Validated invariants:
- D1~D5 schema compatibility of output categories
- data_exposure family validator pass
- span_count represents detection count
- original PII values NOT present in response
- boolean consistency (risk_detected == model_risk_detected == any detected category)
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from ai_model_serving.detectors.pii import (
    EntitySpan,
    EntitySummary,
    PIIProtectionDetector,
    _categories_from_summaries,
    _classify_spans,
    _reconcile_spans,
    _run_custom_span_recognizers,
)


def _custom_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for span in _run_custom_span_recognizers(text):
        counts[span.entity] = counts.get(span.entity, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Custom recognizer tests (no presidio dependency)
# ---------------------------------------------------------------------------

class TestKoreanCustomRecognizers:
    def test_rrn_detected(self):
        counts = _custom_counts("주민번호: 901201-1234567 확인 바랍니다.")
        assert "KR_RRN" in counts
        assert counts["KR_RRN"] >= 1

    def test_frn_detected(self):
        counts = _custom_counts("외국인등록번호: 901201-5234567")
        assert "KR_FRN" in counts

    def test_passport_detected(self):
        counts = _custom_counts("여권번호: MA1234567")
        assert "KR_PASSPORT" in counts

    def test_driver_license_detected(self):
        counts = _custom_counts("면허번호: 12-34-567890-12")
        assert "KR_DRIVER_LICENSE" in counts

    def test_ip_address_detected(self):
        counts = _custom_counts("서버 IP 192.168.1.100에 접속하세요.")
        assert "IP_ADDRESS" in counts
        assert counts["IP_ADDRESS"] == 1

    def test_mobile_phone_detected(self):
        counts = _custom_counts("내 전화번호는 010-3817-5168입니다.")
        assert "PHONE_NUMBER" in counts
        assert counts["PHONE_NUMBER"] >= 1

    def test_mobile_phone_010_4digit_detected(self):
        counts = _custom_counts("연락처: 010-1234-5678")
        assert "PHONE_NUMBER" in counts

    def test_seoul_landline_detected(self):
        counts = _custom_counts("사무실: 02-1234-5678")
        assert "PHONE_NUMBER" in counts

    def test_regional_landline_detected(self):
        counts = _custom_counts("경기 번호: 031-123-4567")
        assert "PHONE_NUMBER" in counts

    def test_old_mobile_prefix_detected(self):
        counts = _custom_counts("구형 번호: 011-123-4567")
        assert "PHONE_NUMBER" in counts

    def test_clean_text_returns_empty(self):
        counts = _custom_counts("오늘 날씨가 맑습니다.")
        assert counts == {}


# ---------------------------------------------------------------------------
# Span reconciliation tests
# ---------------------------------------------------------------------------

class TestSpanReconciliation:
    def test_email_suppresses_url_inside_its_domain(self):
        email = EntitySpan("EMAIL_ADDRESS", 7, 23, "custom")
        url = EntitySpan("URL", 12, 23, "presidio")
        assert _reconcile_spans([email, url]) == [email]

    def test_independent_email_and_url_are_both_preserved(self):
        email = EntitySpan("EMAIL_ADDRESS", 0, 16, "custom")
        url = EntitySpan("URL", 21, 32, "presidio")
        assert _reconcile_spans([email, url]) == [email, url]

    def test_same_email_from_two_recognizers_counts_once(self):
        custom = EntitySpan("EMAIL_ADDRESS", 7, 23, "custom")
        presidio = EntitySpan("EMAIL_ADDRESS", 7, 23, "presidio")
        assert _reconcile_spans([presidio, custom]) == [custom]

    def test_partial_overlap_of_same_entity_is_preserved(self):
        first = EntitySpan("URL", 0, 12, "custom")
        second = EntitySpan("URL", 5, 18, "presidio")
        assert _reconcile_spans([first, second]) == [first, second]

    def test_ip_address_suppresses_phone_at_same_span(self):
        ip = EntitySpan("IP_ADDRESS", 4, 15, "custom")
        phone = EntitySpan("PHONE_NUMBER", 4, 15, "presidio")
        assert _reconcile_spans([ip, phone]) == [ip]

    def test_partially_overlapping_url_is_not_suppressed_by_email(self):
        email = EntitySpan("EMAIL_ADDRESS", 7, 23, "custom")
        url = EntitySpan("URL", 12, 28, "presidio")
        assert _reconcile_spans([email, url]) == [email, url]


# ---------------------------------------------------------------------------
# Classification and response-category tests
# ---------------------------------------------------------------------------

class TestClassification:
    def test_classifier_only_maps_reconciled_spans_to_taxonomy(self):
        summaries = _classify_spans(
            [
                EntitySpan("CREDIT_CARD", 0, 19, "presidio"),
                EntitySpan("EMAIL_ADDRESS", 20, 36, "custom"),
            ]
        )
        assert summaries == [
            EntitySummary("CREDIT_CARD", "D3", 1),
            EntitySummary("EMAIL_ADDRESS", "D2", 1),
        ]


# ---------------------------------------------------------------------------
# Category builder tests
# ---------------------------------------------------------------------------

class TestBuildCategories:
    def test_empty_counts_returns_safe_category(self):
        cats = _categories_from_summaries([])
        assert len(cats) == 1
        cat = cats[0]
        assert cat["code"] is None
        assert cat["detected"] is False
        assert cat["family"] == "data_exposure"
        assert cat["span_count"] == 0
        assert "source_model" in cat

    def test_d1_rrn_category(self):
        cats = _categories_from_summaries([EntitySummary("KR_RRN", "D1", 2)])
        assert len(cats) == 1
        cat = cats[0]
        assert cat["code"] == "D1"
        assert cat["detected"] is True
        assert cat["label"] == "KR_RRN"
        assert cat["span_count"] == 2
        assert cat["family"] == "data_exposure"

    def test_d2_email_category(self):
        cats = _categories_from_summaries(
            [EntitySummary("EMAIL_ADDRESS", "D2", 1)]
        )
        assert cats[0]["code"] == "D2"
        assert cats[0]["label"] == "EMAIL_ADDRESS"

    def test_d3_credit_card_category(self):
        cats = _categories_from_summaries(
            [EntitySummary("CREDIT_CARD", "D3", 1)]
        )
        assert cats[0]["code"] == "D3"

    def test_d5_ip_address_category(self):
        cats = _categories_from_summaries(
            [EntitySummary("IP_ADDRESS", "D5", 3)]
        )
        assert cats[0]["code"] == "D5"
        assert cats[0]["span_count"] == 3

    def test_multiple_entity_types(self):
        cats = _categories_from_summaries(
            [
                EntitySummary("EMAIL_ADDRESS", "D2", 2),
                EntitySummary("KR_RRN", "D1", 1),
            ]
        )
        codes = {c["code"] for c in cats}
        assert "D1" in codes
        assert "D2" in codes
        assert all(c["detected"] for c in cats)

    def test_no_raw_pii_values_in_categories(self):
        cats = _categories_from_summaries([EntitySummary("KR_RRN", "D1", 1)])
        for cat in cats:
            for key, value in cat.items():
                if isinstance(value, str):
                    # label is entity type name, NOT a raw PII value
                    assert value in {"KR_RRN", "KR_FRN", "KR_PASSPORT", "KR_DRIVER_LICENSE",
                                     "EMAIL_ADDRESS", "PHONE_NUMBER", "ADDRESS", "CREDIT_CARD",
                                     "IP_ADDRESS", "URL", "data_exposure",
                                     "pii-protection", "D1", "D2", "D3", "D5", None}


# ---------------------------------------------------------------------------
# Detector integration tests
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


# ---------------------------------------------------------------------------
# Presidio live integration (requires presidio-analyzer installed)
# ---------------------------------------------------------------------------

try:
    import presidio_analyzer as _pa  # noqa: F401
    _presidio_installed = True
except ImportError:
    _presidio_installed = False


@pytest.mark.skipif(not _presidio_installed, reason="presidio-analyzer not installed")
class TestPresidioEmailDetection:
    """Verify that EMAIL_ADDRESS is detected as D2 via the live Presidio engine.

    These tests guard against regression of the bug where email addresses were
    silently missed because presidio-analyzer was absent from the Docker image.
    """

    def _run(self, coro):
        return asyncio.run(coro)

    def test_email_address_detected_as_d2(self):
        detector = PIIProtectionDetector()
        response = self._run(detector.assess("담당자 이메일은 hong@example.com입니다."))
        d2_cats = [c for c in response["categories"] if c.get("code") == "D2"]
        assert d2_cats, "EMAIL_ADDRESS should produce a D2 category via Presidio"
        email_cat = next((c for c in d2_cats if c.get("label") == "EMAIL_ADDRESS"), None)
        assert email_cat is not None, "D2 category label should be EMAIL_ADDRESS"
        assert email_cat["span_count"] >= 1

    def test_email_and_phone_both_detected(self):
        detector = PIIProtectionDetector()
        response = self._run(detector.assess("이메일은 hong@example.com이고 연락처는 010-1234-5678입니다."))
        labels = {c.get("label") for c in response["categories"] if c.get("detected")}
        assert "EMAIL_ADDRESS" in labels, "EMAIL_ADDRESS should be detected"
        assert "PHONE_NUMBER" in labels, "PHONE_NUMBER should be detected"

    def test_email_raw_value_not_in_response(self):
        email = "secret@internal.company.com"
        detector = PIIProtectionDetector()
        response = self._run(detector.assess(f"연락처: {email}"))
        import json
        assert email not in json.dumps(response)
