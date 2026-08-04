"""main-llm 프로필/부팅/제어 관련 계약 테스트.

compose bootstrap 이미지가 default profile의 값과 일치하는지 등, main-model
runtime control이 여러 config/compose 파일에 걸쳐 정합성을 유지하는지 검증한다.
compose는 sidecar reconciliation보다 먼저 시작되므로 이 투영이 어긋나면
콜드 부팅 시점에 바로 문제가 된다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

def test_main_model_profiles_pin_revision_image_and_default_golden_command():
    profiles = yaml.safe_load(
        (ROOT / "configs/main_model_profiles.yaml").read_text(encoding="utf-8")
    )
    assert "@sha256:" in profiles["runtime"]["image"]
    for profile in profiles["profiles"].values():
        assert len(profile["revision"]) == 40
        assert profile["command"][profile["command"].index("--revision") + 1] == profile["revision"]

    compose = yaml.safe_load(
        (ROOT / "ops/compose/full-stack.private-network.yaml").read_text(encoding="utf-8")
    )
    assert compose["services"]["main-llm-vllm"]["command"] == profiles["profiles"][
        profiles["default_profile"]
    ]["command"]


def test_env_templates_follow_catalog_default_profile():
    profiles = yaml.safe_load(
        (ROOT / "configs/main_model_profiles.yaml").read_text(encoding="utf-8")
    )
    expected = f"MAIN_LLM_BOOT_PROFILE={profiles['default_profile']}"
    for name in (".env.example", ".env.local.example", ".env.compose.example"):
        assert expected in (ROOT / name).read_text(encoding="utf-8"), name


def test_sidecar_and_main_runtime_share_the_hugging_face_cache():
    compose = yaml.safe_load(
        (ROOT / "ops/compose/full-stack.private-network.yaml").read_text(
            encoding="utf-8"
        )
    )
    services = compose["services"]
    expected = (
        "${HF_CACHE_DIR:-./model_cache/huggingface}:/root/.cache/huggingface"
    )
    assert expected in services["main-llm-vllm"]["volumes"]
    assert expected in services["admin-sidecar"]["volumes"]
    assert services["admin-sidecar"]["environment"]["HF_HOME"] == (
        "/root/.cache/huggingface"
    )
