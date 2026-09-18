"""원격 full deploy의 shared runtime image promotion/pin 정책을 검증한다."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = "scripts/lib/deploy_env.sh"
_IMAGE_KEYS = (
    "VLLM_UNIFIED_IMAGE_TO_DEPLOY",
    "MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY",
    "AUDIO_VLLM_IMAGE_TO_DEPLOY",
)


def _env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        "VLLM_IMAGE=registry.example/shared@sha256:" + "1" * 64 + "\n"
        "MAIN_MODEL_VLLM_IMAGE_OVERRIDE=registry.example/profile@sha256:" + "4" * 64 + "\n",
        encoding="utf-8",
    )
    return path


def _resolve(env_file: Path, **environment: str) -> tuple[str, str]:
    env = os.environ.copy()
    for key in _IMAGE_KEYS:
        env.pop(key, None)
    env.update(environment)
    env["COMPOSE_ENV_FILE"] = str(env_file)
    command = (
        f"source {SCRIPT}; deploy_resolve_runtime_image_plan; "
        'printf "%s|%s\\n" '
        '"$VLLM_IMAGE_EFFECTIVE" "$MAIN_MODEL_VLLM_IMAGE_OVERRIDE_EFFECTIVE"; '
        'printf "%s|%s" '
        '"${VLLM_IMAGE_PROMOTION:-}" "${MAIN_MODEL_VLLM_IMAGE_OVERRIDE_PROMOTION:-}"'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    effective, promotion = result.stdout.splitlines()
    return effective, promotion


def test_full_deploy_without_image_inputs_preserves_shared_and_profile_override_pins(tmp_path: Path):
    env_file = _env_file(tmp_path)
    shared = "registry.example/shared@sha256:" + "1" * 64
    profile_override = "registry.example/profile@sha256:" + "4" * 64

    effective, promotion = _resolve(env_file)

    assert effective == "|".join([shared, profile_override])
    assert promotion == "|"


def test_unified_artifact_promotes_shared_consumers_and_profile_override_by_default(tmp_path: Path):
    env_file = _env_file(tmp_path)
    unified = "registry.example/unified@sha256:" + "a" * 64

    effective, promotion = _resolve(env_file, VLLM_UNIFIED_IMAGE_TO_DEPLOY=unified)

    assert effective == "|".join([unified, unified])
    assert promotion == "|".join([unified, unified])



def test_profile_override_remains_independent_from_shared_promotion(tmp_path: Path):
    env_file = _env_file(tmp_path)
    unified = "registry.example/unified@sha256:" + "c" * 64
    profile_override = "registry.example/profile-special@sha256:" + "d" * 64

    effective, promotion = _resolve(
        env_file,
        VLLM_UNIFIED_IMAGE_TO_DEPLOY=unified,
        MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY=profile_override,
    )

    assert effective == "|".join([unified, profile_override])
    assert promotion == "|".join([unified, profile_override])


def test_legacy_audio_key_is_read_as_profile_override_during_migration(tmp_path: Path):
    env_file = tmp_path / ".env"
    legacy = "registry.example/legacy-profile@sha256:" + "e" * 64
    env_file.write_text(
        "VLLM_IMAGE=registry.example/shared@sha256:" + "1" * 64 + "\n"
        f"AUDIO_VLLM_IMAGE={legacy}\n",
        encoding="utf-8",
    )

    effective, promotion = _resolve(env_file)

    shared = "registry.example/shared@sha256:" + "1" * 64
    assert effective == "|".join([shared, legacy])
    assert promotion == "|"
