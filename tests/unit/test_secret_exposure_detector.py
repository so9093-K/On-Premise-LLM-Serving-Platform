"""Secret Exposure detector 단위 테스트.

정제된 정규식 패턴, 엔트로피 기반 범용 후보 탐지, entity->D-code 매핑을 다룬다.

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
from ai_model_serving.detectors.secret import SecretExposureDetector, mask_secrets


# ---------------------------------------------------------------------------
# 실제와 비슷한 secret 패턴을 담은 픽스처 텍스트
# ---------------------------------------------------------------------------

OPENAI_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz12345678901234ABCDE"
ANTHROPIC_KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGHIJ"
AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
GITLAB_TOKEN = "glpat-abcdefghijklmnopqrst"
HF_TOKEN = "hf_abcdefghijklmnopqrstuvwxyz12345678"
JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----"
DATABASE_URL = "postgresql://user:p4ssw0rd@db.example.com:5432/mydb"
PASSWORD_ASSIGN = "password=Sup3rS3cr3t!"


def assess(text: str) -> dict:
    return asyncio.run(SecretExposureDetector().assess(text))


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
        # 이름 붙은 패턴에도 대입 키워드에도 안 걸리는 고엔트로피 문자열만 generic이다.
        ("붙여넣은 값 Xq7Fv2Lp9Rt4Wz8Nb1Mc6Yd3Ke5Hg0Ju", "GENERIC_SECRET_CANDIDATE", "D4"),
        # DB 접속 문자열은 자격증명(D4)이 아니라 인프라 노출(D5)로 분류된다.
        (f"접속: {DATABASE_URL}", "DATABASE_URL", "D5"),
    ],
)
def test_secret_is_reported_with_expected_code(text, label, code):
    response = assess(text)
    detected = {c["label"]: c for c in response["categories"] if c["detected"]}
    assert label in detected
    assert detected[label]["code"] == code
    assert detected[label]["family"] == "data_exposure"
    assert detected[label]["span_count"] >= 1


def test_text_without_secrets_reports_no_detection():
    # 저엔트로피 평문은 generic 후보로도 잡히면 안 된다(오탐 경계).
    response = assess("오늘 배포 일정 공유드립니다. 확인 부탁드립니다.")
    assert not [c for c in response["categories"] if c["detected"]]
    assert response["risk_detected"] is False
    assert response["status"] == "completed"


@pytest.mark.parametrize("text", [f"키: {OPENAI_KEY}", "오늘 날씨가 맑습니다."])
def test_detector_output_satisfies_public_risk_contract(text):
    # Gateway가 런타임에 거는 게이트를 그대로 통과시킨다: 필수 필드, 금지 필드,
    # assessment_id 접두사, category 모양, risk/model_risk/attention boolean 일관성,
    # assessment_complete 일관성, data_exposure의 source_model 필수 여부까지 한 번에.
    validate_risk_response(assess(text))


class TestSecretExposureDetector:
    def test_raw_secret_values_absent_from_response(self):
        # secret 종류별로 반복하지 않는다: _build_categories()는 어떤 패턴이 매치됐든
        # label/span_count만 담고 매치된 원문 substring 자체를 절대 참조하지 않으므로,
        # 이 부재 검증은 secret 종류에 무관하게 동일한 코드 경로를 검증한다.
        response_str = json.dumps(assess(f"값: {OPENAI_KEY}"))
        assert OPENAI_KEY not in response_str

    def test_span_count_reflects_multiple_jwt_occurrences(self):
        response = assess(f"첫번째: {JWT} 두번째: {JWT}")
        jwt_cats = [c for c in response["categories"] if c.get("label") == "JWT"]
        assert len(jwt_cats) == 1
        assert jwt_cats[0]["span_count"] == 2

    def test_d4_outranks_d5_in_strongest_code(self):
        # 자격증명(D4)과 인프라 노출(D5)이 함께 잡히면 더 강한 쪽이 대표값이어야 한다.
        response = assess(f"키: {OPENAI_KEY} 접속: {DATABASE_URL}")
        codes = {c["code"] for c in response["categories"] if c["detected"]}
        assert {"D4", "D5"}.issubset(codes)
        assert response["strongest_code"] == "D4"


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
