"""원격 접속 전 배포 요청 정책의 최소 행동 회귀 테스트."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POLICY = "scripts/lib/deploy_request_policy.sh"

_ISOLATED_KEYS = (
    "DEPLOY_MODE",
    "DEPLOY_MODE_REASON",
    "RISK_VLLM_IMAGE_TO_DEPLOY",
    "VLLM_UNIFIED_IMAGE_SHA",
    "VLLM_UNIFIED_IMAGE_TO_DEPLOY",
    "AUDIO_VLLM_IMAGE_TO_DEPLOY",
    "RUN_READY_FULL_SMOKE",
    "DEPLOY_RUNTIME_PROFILE",
    "DEPLOY_DEFERRED_RUNTIMES",
)


def run_policy(command: str, **environment: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # 파이프라인 트리거 시점에 DEPLOY_MODE=full 같은 배포 변수가 이미 export돼
    # 있으면 이 정책 함수가 조기 리턴해 테스트가 파이프라인의 우연한 상태에
    # 좌우된다 -- 여기서 명시적으로 지워서 각 테스트가 지정한 값만 보게 한다.
    for key in _ISOLATED_KEYS:
        env.pop(key, None)
    env |= environment
    return subprocess.run(
        ["bash", "-c", f"source {POLICY}; {command}"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_resolve_mode_defaults_to_rolling_without_vllm_override():
    result = run_policy('deploy_resolve_mode; printf "%s|%s" "$DEPLOY_MODE" "$DEPLOY_MODE_REASON"')

    assert result.returncode == 0
    assert result.stdout == "rolling|platform-only change"


def test_resolve_mode_ignores_unbuilt_vllm_expected_tag():
    result = run_policy(
        'deploy_resolve_mode; printf "%s|%s" "$DEPLOY_MODE" "$DEPLOY_MODE_REASON"',
        VLLM_UNIFIED_IMAGE_SHA="registry.example/vllm@sha256:" + "a" * 64,
    )

    assert result.returncode == 0
    assert result.stdout == "rolling|platform-only change"


def test_resolve_mode_uses_full_for_fresh_vllm_artifact():
    result = run_policy(
        'deploy_resolve_mode; printf "%s|%s" "$DEPLOY_MODE" "$DEPLOY_MODE_REASON"',
        VLLM_UNIFIED_IMAGE_TO_DEPLOY="registry.example/vllm@sha256:" + "a" * 64,
    )

    assert result.returncode == 0
    assert result.stdout == "full|vLLM image override provided"


def test_rolling_deploy_rejects_runtime_startup_policy():
    result = run_policy(
        'DEPLOY_MODE=rolling; RUN_READY_FULL_SMOKE=1; DEPLOY_RUNTIME_PROFILE=full_hot; '
        'deploy_validate_request release-1 5',
    )

    assert result.returncode == 2
    assert "require DEPLOY_MODE=full" in result.stderr


def test_full_deploy_uses_one_unified_image_for_risk_and_audio():
    image = "registry.example/vllm@sha256:" + "b" * 64
    result = run_policy(
        'DEPLOY_MODE=full; deploy_resolve_full_runtime_images; '
        'printf "%s|%s" "$RISK_VLLM_IMAGE_TO_DEPLOY" "$AUDIO_VLLM_IMAGE_TO_DEPLOY"',
        VLLM_UNIFIED_IMAGE_TO_DEPLOY=image,
    )
    assert result.returncode == 0
    assert result.stdout == f"{image}|{image}"
