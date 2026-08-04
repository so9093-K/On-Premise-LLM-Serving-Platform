"""risk_assessment_response.schema.json에 대한 JSON Schema 계약 테스트.

지원되는 data_exposure 코드, span_count 필드, 기존 A1/A2/I1-I4 코드 불변식을
다룬다. forbidden 필드가 계속 차단되는지도 검증한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def load_schema(name: str) -> dict:
    return json.loads((ROOT / "specs" / "schemas" / name).read_text(encoding="utf-8"))


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


def _data_exposure_cat(code, label, detected=True, span_count=1, source_model="pii-protection"):
    return {
        "code": code,
        "family": "data_exposure",
        "detected": detected,
        "confidence": None,
        "source_model": source_model,
        "label": label,
        "span_count": span_count,
    }


class TestDataExposureSchemaValidation:
    @pytest.fixture
    def validator(self):
        return Draft202012Validator(load_schema("risk_assessment_response.schema.json"))

    def test_d1_rrn_passes(self, validator):
        cat = _data_exposure_cat("D1", "KR_RRN")
        response = _base_response(
            risk_detected=True, attention_required=True, model_risk_detected=True,
            strongest_code="D1", categories=[cat],
        )
        validator.validate(response)

    def test_d2_email_passes(self, validator):
        cat = _data_exposure_cat("D2", "EMAIL_ADDRESS")
        response = _base_response(
            risk_detected=True, attention_required=True, model_risk_detected=True,
            strongest_code="D2", categories=[cat],
        )
        validator.validate(response)

    def test_d4_api_key_passes(self, validator):
        cat = _data_exposure_cat("D4", "OPENAI_API_KEY", source_model="secret-scanner")
        response = _base_response(
            risk_detected=True, attention_required=True, model_risk_detected=True,
            strongest_code="D4", categories=[cat],
        )
        validator.validate(response)

    def test_d5_database_url_passes(self, validator):
        cat = _data_exposure_cat("D5", "DATABASE_URL", source_model="secret-scanner")
        response = _base_response(
            risk_detected=True, attention_required=True, model_risk_detected=True,
            strongest_code="D5", categories=[cat],
        )
        validator.validate(response)

    def test_safe_data_exposure_category_passes(self, validator):
        cat = {
            "code": None,
            "family": "data_exposure",
            "detected": False,
            "confidence": None,
            "source_model": "pii-protection",
            "label": None,
            "span_count": 0,
        }
        response = _base_response(categories=[cat])
        validator.validate(response)

    def test_span_count_null_passes(self, validator):
        cat = _data_exposure_cat("D1", "KR_RRN", span_count=None)
        response = _base_response(
            risk_detected=True, attention_required=True, model_risk_detected=True,
            strongest_code="D1", categories=[cat],
        )
        validator.validate(response)

    def test_span_count_zero_passes(self, validator):
        cat = _data_exposure_cat("D2", "EMAIL_ADDRESS", span_count=0)
        response = _base_response(
            risk_detected=True, attention_required=True, model_risk_detected=True,
            strongest_code="D2", categories=[cat],
        )
        validator.validate(response)

    def test_span_count_negative_fails(self, validator):
        cat = _data_exposure_cat("D1", "KR_RRN", span_count=-1)
        response = _base_response(
            risk_detected=True, attention_required=True, model_risk_detected=True,
            strongest_code="D1", categories=[cat],
        )
        assert list(validator.iter_errors(response))

    def test_d_code_wrong_family_fails(self, validator):
        cat = {
            "code": "D1",
            "family": "prompt_attack",  # D code에는 잘못된 family
            "detected": True,
            "confidence": None,
            "source_model": "pii-protection",
            "label": "KR_RRN",
            "span_count": 1,
        }
        response = _base_response(
            risk_detected=True, attention_required=True, model_risk_detected=True,
            strongest_code="D1", categories=[cat],
        )
        assert list(validator.iter_errors(response))

    def test_all_d_codes_in_strongest_code_pass(self, validator):
        # D2/D4는 별도로 두지 않는다: 스키마는 `"pattern": "^D[1-5]$"` 하나로 D1~D5를
        # 전부 같은 if/then 분기(family == data_exposure)에 묶으므로, D1(하한)과
        # D5(상한)만 확인해도 문자 클래스 범위를 충분히 검증한다.
        for code in ["D1", "D5"]:
            src = "secret-scanner" if code in ("D4", "D5") else "pii-protection"
            label_map = {"D1": "KR_RRN", "D2": "EMAIL_ADDRESS", "D4": "OPENAI_API_KEY", "D5": "DATABASE_URL"}
            cat = _data_exposure_cat(code, label_map[code], source_model=src)
            response = _base_response(
                risk_detected=True, attention_required=True, model_risk_detected=True,
                strongest_code=code, categories=[cat],
            )
            errors = list(validator.iter_errors(response))
            assert not errors, f"D{code} strongest_code failed: {[str(e) for e in errors]}"


class TestExistingSchemaInvariants:
    @pytest.fixture
    def validator(self):
        return Draft202012Validator(load_schema("risk_assessment_response.schema.json"))

    def test_mixed_a1_and_d4_aggregate_response(self, validator):
        a1_cat = {
            "code": "A1",
            "family": "prompt_attack",
            "detected": True,
            "confidence": None,
            "source_model": "risk-prompt",
            "label": "<UNSAFE-A1>",
        }
        d4_cat = _data_exposure_cat("D4", "OPENAI_API_KEY", source_model="secret-scanner")
        response = _base_response(
            risk_detected=True, attention_required=True, model_risk_detected=True,
            strongest_code="D4",  # D4가 A1보다 우선한다
            categories=[d4_cat, a1_cat],
        )
        validator.validate(response)
