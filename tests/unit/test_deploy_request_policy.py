"""원격 접속 전 배포 요청 정책의 최소 행동 회귀 테스트."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POLICY = "scripts/lib/deploy_request_policy.sh"
DEPLOY_SCRIPT = ROOT / "scripts/ci/deploy_gitlab_compose.sh"

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


def test_rolling_deploy_rejects_runtime_startup_policy():
    result = run_policy(
        'DEPLOY_MODE=rolling; RUN_READY_FULL_SMOKE=1; DEPLOY_RUNTIME_PROFILE=main_only; '
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


def test_full_deploy_without_new_unified_image_keeps_remote_pins():
    result = run_policy(
        'DEPLOY_MODE=full; deploy_resolve_full_runtime_images; '
        'printf "%s|%s" "${RISK_VLLM_IMAGE_TO_DEPLOY:-}" "${AUDIO_VLLM_IMAGE_TO_DEPLOY:-}"',
    )

    assert result.returncode == 0
    assert result.stdout == "|"


def test_remote_deploy_explicitly_syncs_the_shared_env_before_validation() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    backup = script.index('cp "${COMPOSE_ENV_FILE}" "${ENV_BACKUP}"')
    sync = script.index('make sync-env ENV_FILE="${COMPOSE_ENV_FILE}"')
    validate = script.index('validating gateway settings against synced .env')

    assert backup < sync < validate
    assert 'scripts/config/setup_env.py --sync-runtime-secrets --env-file "${COMPOSE_ENV_FILE}"' in script


def test_remote_deploy_restores_previous_release_when_interrupted() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    handler = script.index("interrupted_after_env_backup()")
    mutation = script.index("# .env에 PLATFORM_IMAGE 갱신")
    assert handler < mutation
    assert "if restore_previous_release; then" in script[handler:mutation]
    assert "candidate release retained because restoration was incomplete" in script[handler:mutation]
    assert "trap 'interrupted_after_env_backup INT 130' INT" in script
    assert "trap 'interrupted_after_env_backup TERM 143' TERM" in script
    assert "trap 'interrupted_after_env_backup HUP 129' HUP" in script
