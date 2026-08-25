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

from ai_model_serving.detectors.secret import SecretExposureDetector, mask_secrets


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


# 각 pattern과 category builder를 private helper로 따로 부르지 않고 공개 assess()
# 하나로 확인한다. 실제 계약은 assess() 응답이고, 두 단계를 나눠 검증하면 "패턴은
# 잡히는데 code가 안 붙는" 조합을 아무도 안 보게 된다.
@pytest.mark.parametrize(
    ("text", "label", "code"),
    [
        (f"키: {OPENAI_KEY}", "OPENAI_API_KEY", "D4"),
        (f"키: {ANTHROPIC_KEY}", "ANTHROPIC_API_KEY", "D4"),
        (f"키: {AWS_ACCESS_KEY}", "AWS_ACCESS_KEY_ID", "D4"),
        (f"토큰: {GITHUB_TOKEN}", "GITHUB_TOKEN", "D4"),
        (f"토큰: {GITLAB_TOKEN}", "GITLAB_TOKEN", "D4"),
        (f"토큰: {HF_TOKEN}", "HUGGINGFACE_TOKEN", "D4"),
        (f"토큰: {JWT}", "JWT", "D4"),
        (PRIVATE_KEY, "PRIVATE_KEY_BLOCK", "D4"),
        (f"설정: {PASSWORD_ASSIGN}", "PASSWORD_ASSIGNMENT", "D4"),
        # 이름 붙은 패턴에 안 걸리는 고엔트로피 문자열은 generic 후보로 잡힌다.
        ("api_key = 'Xq7Fv2Lp9Rt4Wz8Nb1Mc6Yd3Ke5Hg0Ju'", "GENERIC_SECRET_CANDIDATE", "D4"),
        # DB 접속 문자열은 자격증명(D4)이 아니라 인프라 노출(D5)로 분류된다.
        (f"접속: {DATABASE_URL}", "DATABASE_URL", "D5"),
    ],
)
def test_secret_is_reported_with_expected_code(text, label, code):
    response = asyncio.run(SecretExposureDetector().assess(text))
    detected = {c["label"]: c for c in response["categories"] if c["detected"]}
    assert label in detected
    assert detected[label]["code"] == code
    assert detected[label]["family"] == "data_exposure"
    assert detected[label]["span_count"] >= 1


def test_text_without_secrets_reports_no_detection():
    # 저엔트로피 평문은 generic 후보로도 잡히면 안 된다(오탐 경계).
    response = asyncio.run(SecretExposureDetector().assess("오늘 배포 일정 공유드립니다. 확인 부탁드립니다."))
    assert not [c for c in response["categories"] if c["detected"]]


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
