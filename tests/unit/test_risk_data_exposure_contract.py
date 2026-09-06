"""Data exposure 응답이 공개 risk contract를 통과하는지 검증한다.

private category helper의 내부 분기 대신 ``validate_risk_response()``를 통해
실제 Risk Adapter와 Gateway가 소비하는 response 계약만 고정한다.
"""
from __future__ import annotations

import pytest

from ai_model_serving.contracts.risk import validate_risk_response
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


class TestValidateRiskResponseWithDataExposure:
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
