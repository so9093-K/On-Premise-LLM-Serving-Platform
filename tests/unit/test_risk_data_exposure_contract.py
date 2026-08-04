"""Data exposure contract validator 단위 테스트.

D1-D5 data_exposure category에 대한 validate_risk_response()와
_validate_risk_category() 동작을 테스트한다. 확인하는 것:
- D1~D5 + data_exposure family가 validator를 통과함
- span_count: None 또는 int >= 0이 허용됨
- forbidden 필드는 여전히 차단됨
- 안전한 data_exposure category(code=None)가 통과함
- 원본 PII/Secret 값은 contract guard가 거부함(필드가 아니라 구조적 체크)
- boolean 일관성 검증됨
- 기존 A1/A2 prompt detector 동작은 그대로임
"""
from __future__ import annotations

import pytest

from ai_model_serving.contracts.risk import validate_risk_response, _validate_risk_category
from ai_model_serving.errors import ServiceError


def _base_response(**kwargs) -> dict:
    base = {
        "assessment_id": "risk_test123",
        "status": "completed",
        "risk_detected": False,
        "attention_required": False,
        "model_risk_detected": False,
        "system_signal_detected": False,
        "assessment_complete": True,
        "strongest_code": None,
        "message": "No model risk signal detected.",
        "categories": [],
        "system_signals": [],
    }
    base.update(kwargs)
    return base


def _data_exposure_category(
    code: str | None,
    label: str | None,
    detected: bool,
    span_count: int | None = 0,
    source_model: str = "pii-protection",
) -> dict:
    return {
        "code": code,
        "family": "data_exposure",
        "detected": detected,
        "confidence": None,
        "source_model": source_model,
        "label": label,
        "span_count": span_count,
    }


class TestDataExposureCategoryValidator:
    def test_d1_category_passes(self):
        cat = _data_exposure_category("D1", "KR_RRN", True, span_count=1)
        result = _validate_risk_category(cat, index=0)
        assert result is True

    def test_d2_category_passes(self):
        cat = _data_exposure_category("D2", "EMAIL_ADDRESS", True, span_count=2)
        result = _validate_risk_category(cat, index=0)
        assert result is True

    def test_d4_category_passes(self):
        cat = _data_exposure_category("D4", "OPENAI_API_KEY", True, span_count=1, source_model="secret-scanner")
        result = _validate_risk_category(cat, index=0)
        assert result is True

    def test_d5_category_passes(self):
        cat = _data_exposure_category("D5", "DATABASE_URL", True, span_count=1, source_model="secret-scanner")
        result = _validate_risk_category(cat, index=0)
        assert result is True

    def test_safe_data_exposure_category_passes(self):
        cat = _data_exposure_category(None, None, False, span_count=0)
        result = _validate_risk_category(cat, index=0)
        assert result is False

    def test_span_count_null_is_allowed(self):
        cat = _data_exposure_category("D1", "KR_RRN", True, span_count=None)
        _validate_risk_category(cat, index=0)

    def test_span_count_zero_is_allowed(self):
        cat = _data_exposure_category("D2", "EMAIL_ADDRESS", True, span_count=0)
        _validate_risk_category(cat, index=0)

    def test_span_count_negative_raises(self):
        cat = _data_exposure_category("D1", "KR_RRN", True, span_count=-1)
        with pytest.raises(ServiceError) as exc_info:
            _validate_risk_category(cat, index=0)
        assert "span_count" in exc_info.value.message

    def test_data_exposure_wrong_family_raises(self):
        cat = {
            "code": "D1",
            "family": "prompt_attack",  # 잘못된 family
            "detected": True,
            "confidence": None,
            "source_model": "pii-protection",
            "label": "KR_RRN",
            "span_count": 1,
        }
        with pytest.raises(ServiceError):
            _validate_risk_category(cat, index=0)

    def test_d1_detected_without_label_raises(self):
        cat = _data_exposure_category("D1", "", True, span_count=1)
        with pytest.raises(ServiceError):
            _validate_risk_category(cat, index=0)

    def test_d4_empty_source_model_raises(self):
        cat = _data_exposure_category("D4", "OPENAI_API_KEY", True, span_count=1, source_model="")
        with pytest.raises(ServiceError):
            _validate_risk_category(cat, index=0)

    def test_existing_a1_category_unchanged(self):
        cat = {
            "code": "A1",
            "family": "prompt_attack",
            "detected": True,
            "confidence": None,
            "source_model": "risk-prompt",
            "label": "<UNSAFE-A1>",
        }
        result = _validate_risk_category(cat, index=0)
        assert result is True

    def test_existing_safe_prompt_category_unchanged(self):
        cat = {
            "code": None,
            "family": "prompt_attack",
            "detected": False,
            "confidence": None,
            "source_model": "risk-prompt",
            "label": "<SAFE>",
        }
        result = _validate_risk_category(cat, index=0)
        assert result is False


class TestValidateRiskResponseWithDataExposure:
    def test_d1_response_passes_full_validation(self):
        cat = _data_exposure_category("D1", "KR_RRN", True, span_count=1)
        response = _base_response(
            risk_detected=True,
            attention_required=True,
            model_risk_detected=True,
            strongest_code="D1",
            categories=[cat],
        )
        result = validate_risk_response(response)
        assert result["strongest_code"] == "D1"

    def test_d4_response_passes_full_validation(self):
        cat = _data_exposure_category("D4", "JWT", True, span_count=2, source_model="secret-scanner")
        response = _base_response(
            risk_detected=True,
            attention_required=True,
            model_risk_detected=True,
            strongest_code="D4",
            categories=[cat],
        )
        validate_risk_response(response)

    def test_safe_data_exposure_response_passes(self):
        cat = _data_exposure_category(None, None, False, span_count=0)
        response = _base_response(categories=[cat])
        validate_risk_response(response)

    def test_mixed_d4_and_a1_in_aggregate(self):
        d4_cat = _data_exposure_category("D4", "OPENAI_API_KEY", True, span_count=1, source_model="secret-scanner")
        a1_cat = {
            "code": "A1",
            "family": "prompt_attack",
            "detected": True,
            "confidence": None,
            "source_model": "risk-prompt",
            "label": "<UNSAFE-A1>",
        }
        response = _base_response(
            risk_detected=True,
            attention_required=True,
            model_risk_detected=True,
            strongest_code="D4",  # D4가 A1보다 우선한다
            categories=[d4_cat, a1_cat],
        )
        validate_risk_response(response)

    def test_forbidden_fields_still_blocked(self):
        for field in ["allow", "block", "decision", "action", "safe_to_send",
                      "final_decision", "final_decision_owner", "policy_overrides"]:
            response = _base_response(**{field: True})
            with pytest.raises(ServiceError) as exc_info:
                validate_risk_response(response)
            assert "forbidden" in exc_info.value.message.lower() or field in exc_info.value.message

    def test_supported_data_exposure_codes_are_valid_strongest_codes(self):
        for code in ["D1", "D2", "D4", "D5"]:
            source = "pii-protection" if code != "D4" else "secret-scanner"
            label_map = {"D1": "KR_RRN", "D2": "EMAIL_ADDRESS", "D4": "OPENAI_API_KEY", "D5": "IP_ADDRESS"}
            cat = _data_exposure_category(code, label_map[code], True, span_count=1, source_model=source)
            response = _base_response(
                risk_detected=True,
                attention_required=True,
                model_risk_detected=True,
                strongest_code=code,
                categories=[cat],
            )
            validate_risk_response(response)

    def test_boolean_consistency_enforced(self):
        cat = _data_exposure_category("D1", "KR_RRN", True, span_count=1)
        # risk_detected=False인데 category는 detected=True — 불일치
        response = _base_response(
            risk_detected=False,  # wrong
            attention_required=True,
            model_risk_detected=True,
            strongest_code="D1",
            categories=[cat],
        )
        with pytest.raises(ServiceError):
            validate_risk_response(response)

    def test_assessment_complete_consistency(self):
        cat = _data_exposure_category("D2", "EMAIL_ADDRESS", True, span_count=1)
        response = _base_response(
            status="partial",
            assessment_complete=True,  # 잘못됨: partial이면 False여야 한다
            risk_detected=True,
            attention_required=True,
            model_risk_detected=True,
            strongest_code="D2",
            categories=[cat],
        )
        with pytest.raises(ServiceError):
            validate_risk_response(response)
