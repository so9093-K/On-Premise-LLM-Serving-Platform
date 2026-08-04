"""main-llm 프로필/부팅/제어 관련 계약 테스트.

compose bootstrap 이미지가 default profile의 값과 일치하는지 등, main-model
runtime control이 여러 config/compose 파일에 걸쳐 정합성을 유지하는지 검증한다.
compose는 sidecar reconciliation보다 먼저 시작되므로 이 투영이 어긋나면
콜드 부팅 시점에 바로 문제가 된다.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _compose_config(*files: str) -> dict:
    command = ["docker", "compose"]
    for file in files:
        command += ["-f", file]
    command += ["--env-file", ".env", "config", "--format", "json"]
    env = os.environ.copy()
    env["COMPOSE_SERVICE_ENV_FILE"] = str((ROOT / ".env").resolve())
    return json.loads(
        subprocess.check_output(command, cwd=ROOT, env=env, text=True)
    )


@pytest.mark.docker
def test_private_network_does_not_publish_vllm_or_sidecar_ports():
    config = _compose_config("ops/compose/full-stack.private-network.yaml")
    services = config["services"]
    for name in (
        "main-llm-vllm",
        "embedding-vllm",
        "embedding-ko-vllm",
        "risk-prompt-vllm",
        "admin-sidecar",
    ):
        assert not services[name].get("ports"), name


@pytest.mark.docker
def test_master_open_publishes_vllm_but_not_sidecar():
    config = _compose_config(
        "ops/compose/full-stack.private-network.yaml",
        "ops/compose/overrides/exposure.master-open.yaml",
    )
    services = config["services"]
    expected = {
        "main-llm-vllm": 9401,
        "embedding-vllm": 9402,
        "risk-prompt-vllm": 9403,
        "embedding-ko-vllm": 9406,
    }
    for name, published in expected.items():
        assert any(int(port["published"]) == published for port in services[name]["ports"])
    assert not services["admin-sidecar"].get("ports")


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


def test_main_model_compose_bootstrap_image_matches_default_profile_fallback():
    """compose는 sidecar reconciliation보다 먼저 시작되므로, 이 projection은 drift가 없어야 한다."""
    profiles = yaml.safe_load(
        (ROOT / "configs/main_model_profiles.yaml").read_text(encoding="utf-8")
    )
    compose = yaml.safe_load(
        (ROOT / "ops/compose/full-stack.private-network.yaml").read_text(encoding="utf-8")
    )
    default_profile = profiles["profiles"][profiles["default_profile"]]
    assert default_profile["image"] == "${AUDIO_VLLM_IMAGE}"
    assert compose["services"]["main-llm-vllm"]["image"] == (
        "${AUDIO_VLLM_IMAGE:-" + profiles["runtime"]["image"] + "}"
    )


def test_env_templates_follow_catalog_default_profile():
    profiles = yaml.safe_load(
        (ROOT / "configs/main_model_profiles.yaml").read_text(encoding="utf-8")
    )
    expected = f"MAIN_LLM_BOOT_PROFILE={profiles['default_profile']}"
    for name in (".env.example", ".env.local.example", ".env.compose.example"):
        assert expected in (ROOT / name).read_text(encoding="utf-8"), name


def test_ci_deploys_gateway_and_sidecar_together_and_exports_image_digest():
    pipeline = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts/ci/deploy_gitlab_compose.sh").read_text(encoding="utf-8")
    assert "PLATFORM_IMAGE_DIGEST" in pipeline
    assert "admin-sidecar" in deploy
    assert "compose_run up -d --no-deps admin-sidecar" in deploy
    assert "render_main_model_boot_override.py" in deploy
    assert "ai_model_serving.model_cache_cli" in deploy
    assert "configure_release_context \"${DEPLOY_PATH}/current\"" in deploy
    assert "activating release ${RELEASE_ID} before Compose convergence" in deploy
    assert 'expected_context="${DEPLOY_PATH}/current/ops/compose"' in deploy
    assert "docker image prune" not in deploy


def test_vllm_unified_dependency_pins_have_one_canonical_source():
    images = yaml.safe_load(
        (ROOT / "configs/recommended_images.yaml").read_text(encoding="utf-8")
    )["images"]
    pins = images["vllm"]["compatibility_pins"]
    assert set(pins) == {"transformers", "huggingface_hub", "transformers_min"}
    assert all(str(value).strip() for value in pins.values())
    assert "compatibility_pins" not in images["risk_vllm"]
    assert "base_image_default" not in images["risk_vllm"]
    assert "base_image_default" not in images["embedding_ko_vllm"]
    pipeline = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert "print_vllm_unified_compatibility.py" in pipeline
    assert "VLLM_BASE_IMAGE:" not in pipeline
    assert "VLLM_UNIFIED_IMAGE_SHA" in pipeline
    assert "VLLM_UNIFIED_IMAGE_REF" in pipeline
    assert "RISK_VLLM_IMAGE_SHA" not in pipeline
    assert "AUDIO_VLLM_IMAGE_SHA" not in pipeline
    ci_build = (ROOT / "scripts/ci/build_vllm_derived_images.sh").read_text(encoding="utf-8")
    assert "--key base_image" in ci_build
    assert str(images["vllm"]["base_image_default"]) not in ci_build
    assert "build/vllm-unified-image.env" in ci_build
    assert "full deploy requires build/vllm-unified-image.env" in pipeline
    deploy_policy = (ROOT / "scripts/lib/deploy_recreate_policy.sh").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts/ci/deploy_gitlab_compose.sh").read_text(encoding="utf-8")
    assert "deploy_has_fresh_unified_image_artifact" in deploy_policy
    assert '"ops/images/vllm-unified/requirements.media.lock"' in deploy
    assert "VLLM_UNIFIED_IMAGE_TO_DEPLOY" in deploy
    assert 'VLLM_UNIFIED_IMAGE_TO_DEPLOY="${VLLM_UNIFIED_IMAGE_TO_DEPLOY:-}" \\' in deploy


def test_boot_projection_is_temporary_and_sidecar_default_is_catalog_driven():
    compose = yaml.safe_load(
        (ROOT / "ops/compose/full-stack.private-network.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert compose["services"]["admin-sidecar"]["environment"][
        "MAIN_LLM_BOOT_PROFILE"
    ] == "${MAIN_LLM_BOOT_PROFILE:-}"
    compose_up = (ROOT / "scripts/compose/compose_up.sh").read_text(encoding="utf-8")
    assert "mktemp" in compose_up
    assert "trap 'rm -f \"$MAIN_MODEL_BOOT_OVERRIDE\"' EXIT" in compose_up


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
