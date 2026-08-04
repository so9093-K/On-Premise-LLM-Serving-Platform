"""Secret Exposure detector 단위 테스트.

정제된 정규식 패턴, 엔트로피 기반 범용 후보 탐지, entity->D-code 매핑을
다룬다. 모든 테스트가 다음을 확인한다:
- 원본 secret 값이 response에 절대 나타나지 않는다
- span_count가 탐지 개수를 나타낸다
- D4/D5 코드가 올바르게 부여된다
- boolean 일관성이 유지된다
- forbidden 필드가 없다
"""
from __future__ import annotations

import asyncio
import json

import pytest

from ai_model_serving.detectors.secret import (
    SecretExposureDetector,
    _scan_text,
    _build_categories,
    _shannon_entropy,
    mask_secrets,
)


# ---------------------------------------------------------------------------
# 실제와 비슷한 secret 패턴을 담은 픽스처 텍스트
# ---------------------------------------------------------------------------

OPENAI_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz12345678901234ABCDE"
ANTHROPIC_KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGHIJ"
AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz1234567890AB"
GITLAB_TOKEN = "glpat-abcdefghijklmnopqrst"
HF_TOKEN = "hf_abcdefghijklmnopqrstuvwxyz12345678"
JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----"
DATABASE_URL = "postgresql://user:p4ssw0rd@db.example.com:5432/mydb"
PASSWORD_ASSIGN = "password=Sup3rS3cr3t!"


class TestScanText:
    def test_openai_key_detected(self):
        counts = _scan_text(f"API_KEY={OPENAI_KEY}")
        assert "OPENAI_API_KEY" in counts

    def test_anthropic_key_detected(self):
        counts = _scan_text(f"ANTHROPIC_API_KEY={ANTHROPIC_KEY}")
        assert "ANTHROPIC_API_KEY" in counts

    def test_aws_access_key_detected(self):
        counts = _scan_text(f"AWS_ACCESS_KEY_ID={AWS_ACCESS_KEY}")
        assert "AWS_ACCESS_KEY_ID" in counts

    def test_github_token_detected(self):
        counts = _scan_text(f"export TOKEN={GITHUB_TOKEN}")
        assert "GITHUB_TOKEN" in counts

    def test_gitlab_token_detected(self):
        counts = _scan_text(f"GITLAB_TOKEN={GITLAB_TOKEN}")
        assert "GITLAB_TOKEN" in counts

    def test_huggingface_token_detected(self):
        counts = _scan_text(f"HF_TOKEN={HF_TOKEN}")
        assert "HUGGINGFACE_TOKEN" in counts

    def test_jwt_detected(self):
        counts = _scan_text(f"Authorization: Bearer {JWT}")
        assert "JWT" in counts

    def test_private_key_block_detected(self):
        counts = _scan_text(f"키 내용:\n{PRIVATE_KEY}\nMIIEo...\n-----END RSA PRIVATE KEY-----")
        assert "PRIVATE_KEY_BLOCK" in counts

    def test_database_url_detected_as_d5(self):
        counts = _scan_text(f"DATABASE_URL={DATABASE_URL}")
        assert "DATABASE_URL" in counts

    def test_password_assignment_detected(self):
        counts = _scan_text(f"config: {PASSWORD_ASSIGN}")
        assert "PASSWORD_ASSIGNMENT" in counts

    def test_clean_text_returns_empty(self):
        counts = _scan_text("오늘 날씨가 맑고 기온이 25도입니다.")
        assert counts == {}

    def test_multiple_secrets_counted(self):
        text = f"{OPENAI_KEY}\n{AWS_ACCESS_KEY}"
        counts = _scan_text(text)
        assert "OPENAI_API_KEY" in counts
        assert "AWS_ACCESS_KEY_ID" in counts


class TestShannonEntropy:
    def test_high_entropy_random_string(self):
        s = "aB3xQ7mN2pR9kL5vW1cY8fH4gJ6tD0"
        assert _shannon_entropy(s) > 4.0

    def test_low_entropy_repeating_string(self):
        s = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        assert _shannon_entropy(s) < 1.0

    def test_empty_string_returns_zero(self):
        assert _shannon_entropy("") == 0.0


class TestBuildCategories:
    def test_empty_counts_returns_safe_category(self):
        cats = _build_categories({})
        assert len(cats) == 1
        cat = cats[0]
        assert cat["code"] is None
        assert cat["detected"] is False
        assert cat["family"] == "data_exposure"
        assert cat["span_count"] == 0

    def test_openai_key_maps_to_d4(self):
        cats = _build_categories({"OPENAI_API_KEY": 1})
        assert cats[0]["code"] == "D4"
        assert cats[0]["label"] == "OPENAI_API_KEY"
        assert cats[0]["detected"] is True

    def test_database_url_maps_to_d5(self):
        cats = _build_categories({"DATABASE_URL": 1})
        assert cats[0]["code"] == "D5"
        assert cats[0]["label"] == "DATABASE_URL"

    def test_jwt_maps_to_d4(self):
        cats = _build_categories({"JWT": 2})
        assert cats[0]["code"] == "D4"
        assert cats[0]["span_count"] == 2

    def test_private_key_block_maps_to_d4(self):
        cats = _build_categories({"PRIVATE_KEY_BLOCK": 1})
        assert cats[0]["code"] == "D4"

    def test_generic_candidate_maps_to_d4(self):
        cats = _build_categories({"GENERIC_SECRET_CANDIDATE": 1})
        assert cats[0]["code"] == "D4"


class TestSecretExposureDetector:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_openai_key_returns_d4_signal(self):
        detector = SecretExposureDetector()
        response = self._run(detector.assess(f"키: {OPENAI_KEY}"))
        assert response["risk_detected"] is True
        d4_cats = [c for c in response["categories"] if c.get("code") == "D4"]
        assert d4_cats
        assert d4_cats[0]["label"] == "OPENAI_API_KEY"

    def test_database_url_returns_d5_signal(self):
        detector = SecretExposureDetector()
        response = self._run(detector.assess(f"connect: {DATABASE_URL}"))
        assert response["risk_detected"] is True
        d5_cats = [c for c in response["categories"] if c.get("code") == "D5"]
        assert d5_cats

    def test_clean_text_returns_safe_response(self):
        detector = SecretExposureDetector()
        response = self._run(detector.assess("오늘 날씨가 맑습니다."))
        assert response["risk_detected"] is False
        assert response["model_risk_detected"] is False
        assert response["status"] == "completed"

    def test_raw_secret_values_absent_from_response(self):
        # secret 종류별로 반복하지 않는다: _build_categories()는 어떤 패턴이 매치됐든
        # label/span_count만 담고 매치된 원문 substring 자체를 절대 참조하지 않으므로,
        # 이 부재 검증은 secret 종류에 무관하게 동일한 코드 경로를 검증한다.
        # 종류별 탐지(regex) 정확성은 TestScanText에서 이미 개별로 검증한다.
        detector = SecretExposureDetector()
        secret = OPENAI_KEY
        response = self._run(detector.assess(f"값: {secret}"))
        response_str = json.dumps(response)
        assert secret not in response_str, f"raw secret found in response for {secret[:20]}..."

    def test_span_count_reflects_multiple_jwt_occurrences(self):
        detector = SecretExposureDetector()
        response = self._run(detector.assess(f"첫번째: {JWT} 두번째: {JWT}"))
        d4_cats = [c for c in response["categories"] if c.get("code") == "D4" and c.get("label") == "JWT"]
        assert len(d4_cats) == 1
        assert d4_cats[0]["span_count"] == 2

    def test_boolean_consistency_when_detected(self):
        detector = SecretExposureDetector()
        response = self._run(detector.assess(f"키: {OPENAI_KEY}"))
        detected_count = sum(1 for c in response["categories"] if c["detected"])
        assert response["risk_detected"] == (detected_count > 0)
        assert response["model_risk_detected"] == response["risk_detected"]
        assert response["attention_required"] == (response["model_risk_detected"] or response["system_signal_detected"])

    def test_forbidden_fields_not_in_response(self):
        detector = SecretExposureDetector()
        response = self._run(detector.assess(f"secret={OPENAI_KEY}"))
        forbidden = {"allow", "block", "decision", "action", "safe_to_send", "policy_overrides"}
        assert not forbidden.intersection(response)

    def test_source_model_field_present_and_non_empty(self):
        detector = SecretExposureDetector()
        response = self._run(detector.assess(f"key={OPENAI_KEY}"))
        for cat in response["categories"]:
            assert isinstance(cat.get("source_model"), str)
            assert cat["source_model"]

    def test_d4_has_higher_priority_than_a_codes_in_strongest_code(self):
        # 탐지되면 D4가 strongest_code로 나와야 한다
        detector = SecretExposureDetector()
        response = self._run(detector.assess(f"key={OPENAI_KEY}"))
        assert response["strongest_code"] == "D4"

    def test_assessment_id_starts_with_risk(self):
        detector = SecretExposureDetector()
        response = self._run(detector.assess("test"))
        assert response["assessment_id"].startswith("risk_")


class TestMaskSecrets:
    def test_replaces_named_pattern_with_label(self):
        masked = mask_secrets(f"key={OPENAI_KEY}")
        assert OPENAI_KEY not in masked
        assert "[OPENAI_API_KEY]" in masked

    def test_multiple_secrets_masked_without_offset_corruption(self):
        masked = mask_secrets(f"a={AWS_ACCESS_KEY} b={GITHUB_TOKEN}")
        assert AWS_ACCESS_KEY not in masked
        assert GITHUB_TOKEN not in masked
        assert "[AWS_ACCESS_KEY_ID]" in masked
        assert "[GITHUB_TOKEN]" in masked

    def test_text_without_secrets_is_unchanged(self):
        text = "오늘 날씨가 좋습니다"
        assert mask_secrets(text) == text
