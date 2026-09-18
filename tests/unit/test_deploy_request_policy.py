"""원격 접속 전 배포 요청 정책의 최소 행동 회귀 테스트."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POLICY = "scripts/lib/deploy_request_policy.sh"
PLATFORM_DIGEST = "registry.example/platform@sha256:" + "f" * 64

_ISOLATED_KEYS = (
    "DEPLOY_MODE",
    "DEPLOY_MODE_REASON",
    "PLATFORM_IMAGE_TO_DEPLOY",
    "RISK_VLLM_IMAGE_TO_DEPLOY",
    "VLLM_UNIFIED_IMAGE_TO_DEPLOY",
    "MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY",
    "AUDIO_VLLM_IMAGE_TO_DEPLOY",
    "RUNTIME_STARTUP_PROFILE",
    "DEPLOY_RUNTIME_PROFILE",
    "DEPLOY_DEFERRED_RUNTIMES",
)


def run_policy(command: str, **environment: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # 호출 환경에 DEPLOY_MODE=full 같은 배포 변수가 이미 export돼 있으면 이 정책
    # 함수가 조기 리턴해 테스트가 부모 process의 우연한 상태에
    # 좌우된다 -- 여기서 명시적으로 지워서 각 테스트가 지정한 값만 보게 한다.
    for key in _ISOLATED_KEYS:
        env.pop(key, None)
    env["PLATFORM_IMAGE_TO_DEPLOY"] = PLATFORM_DIGEST
    env |= environment
    return subprocess.run(
        ["bash", "-c", f"source {POLICY}; {command}"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_resolve_mode_defaults_to_full_without_explicit_mode():
    result = run_policy('deploy_resolve_mode; printf "%s|%s" "$DEPLOY_MODE" "$DEPLOY_MODE_REASON"')

    assert result.returncode == 0
    assert result.stdout == "full|default full deployment policy"


def test_resolve_mode_keeps_explicit_rolling_override():
    result = run_policy(
        'deploy_resolve_mode; printf "%s|%s" "$DEPLOY_MODE" "$DEPLOY_MODE_REASON"',
        DEPLOY_MODE="rolling",
    )

    assert result.returncode == 0
    assert result.stdout == "rolling|"


def test_fresh_unified_image_promotes_rolling_request_to_full():
    image = "registry.example/vllm@sha256:" + "c" * 64
    result = run_policy(
        'deploy_resolve_mode; printf "%s|%s" "$DEPLOY_MODE" "$DEPLOY_MODE_REASON"',
        DEPLOY_MODE="rolling",
        VLLM_UNIFIED_IMAGE_TO_DEPLOY=image,
    )

    assert result.returncode == 0
    assert result.stdout == "full|fresh unified vLLM image artifact"


def test_mutable_platform_image_is_rejected_before_remote_mutation():
    result = run_policy(
        "DEPLOY_MODE=full; deploy_validate_request release-1 5",
        PLATFORM_IMAGE_TO_DEPLOY="registry.example/platform:latest",
    )

    assert result.returncode == 2
    assert "PLATFORM_IMAGE_TO_DEPLOY must be an immutable registry digest" in result.stderr


def test_runtime_promotion_inputs_require_registry_digests():
    for key in (
        "VLLM_UNIFIED_IMAGE_TO_DEPLOY",
        "MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY",
    ):
        result = run_policy(
            "DEPLOY_MODE=full; deploy_validate_request release-1 5",
            **{key: "registry.example/runtime:release"},
        )

        assert result.returncode == 2
        assert f"{key} must be an immutable registry digest" in result.stderr


def test_retired_risk_runtime_promotion_input_is_rejected():
    result = run_policy(
        'DEPLOY_MODE=full; deploy_validate_request release-1 5',
        RISK_VLLM_IMAGE_TO_DEPLOY="registry.example/legacy@sha256:" + "b" * 64,
    )

    assert result.returncode == 2
    assert "RISK_VLLM_IMAGE_TO_DEPLOY is retired" in result.stderr
    assert "Use VLLM_UNIFIED_IMAGE_TO_DEPLOY" in result.stderr


def test_rolling_deploy_rejects_runtime_startup_policy():
    result = run_policy(
        'DEPLOY_MODE=rolling; RUNTIME_STARTUP_PROFILE=main_only; '
        'deploy_validate_request release-1 5',
    )

    assert result.returncode == 2
    assert "require DEPLOY_MODE=full" in result.stderr


def test_rolling_deploy_rejects_runtime_image_promotion_input():
    result = run_policy(
        'DEPLOY_MODE=rolling; deploy_validate_request release-1 5',
        MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY="registry.example/profile@sha256:" + "a" * 64,
    )

    assert result.returncode == 2
    assert "runtime image promotion inputs require DEPLOY_MODE=full" in result.stderr


def test_full_deploy_accepts_immutable_shared_and_profile_override_inputs():
    result = run_policy(
        'DEPLOY_MODE=full; deploy_validate_request release-1 5',
        VLLM_UNIFIED_IMAGE_TO_DEPLOY="registry.example/unified@sha256:" + "a" * 64,
        MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY="registry.example/profile@sha256:" + "b" * 64,
    )

    assert result.returncode == 0


def test_legacy_audio_promotion_input_is_accepted_as_compatibility_alias():
    image = "registry.example/profile@sha256:" + "d" * 64
    result = run_policy(
        'DEPLOY_MODE=full; deploy_validate_request release-1 5; '
        'printf "%s" "$MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY"',
        AUDIO_VLLM_IMAGE_TO_DEPLOY=image,
    )

    assert result.returncode == 0
    assert result.stdout == image


def test_conflicting_legacy_and_canonical_profile_image_inputs_are_rejected():
    result = run_policy(
        'DEPLOY_MODE=full; deploy_validate_request release-1 5',
        MAIN_MODEL_VLLM_IMAGE_OVERRIDE_TO_DEPLOY="registry.example/profile@sha256:" + "a" * 64,
        AUDIO_VLLM_IMAGE_TO_DEPLOY="registry.example/legacy@sha256:" + "b" * 64,
    )

    assert result.returncode == 2
    assert "conflicts with legacy AUDIO_VLLM_IMAGE_TO_DEPLOY" in result.stderr


def test_legacy_deploy_runtime_profile_is_normalized_to_canonical_input():
    result = run_policy(
        'DEPLOY_MODE=full; deploy_validate_request release-1 5; '
        'printf "%s" "$RUNTIME_STARTUP_PROFILE"',
        DEPLOY_RUNTIME_PROFILE="retrieval_ready",
    )

    assert result.returncode == 0
    assert result.stdout == "retrieval_ready"


def test_conflicting_runtime_startup_profile_alias_is_rejected():
    result = run_policy(
        'DEPLOY_MODE=full; deploy_validate_request release-1 5',
        RUNTIME_STARTUP_PROFILE="main_only",
        DEPLOY_RUNTIME_PROFILE="retrieval_ready",
    )

    assert result.returncode == 2
    assert "conflicts with legacy DEPLOY_RUNTIME_PROFILE" in result.stderr
