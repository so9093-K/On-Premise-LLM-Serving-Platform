from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_CONTEXT = ROOT / "scripts" / "lib" / "compose_context.sh"
DEPLOY_ENV = ROOT / "scripts" / "lib" / "deploy_env.sh"
APPLY_REMOTE_RELEASE = ROOT / "scripts" / "deploy" / "apply_remote_release.sh"
COMPOSE_FILE = ROOT / "ops" / "compose" / "full-stack.private-network.yaml"


def _bash(command: str) -> subprocess.CompletedProcess[str]:
    process_env = dict(os.environ)
    for key in (
        "VLLM_IMAGE",
        "EMBEDDING_KO_VLLM_IMAGE",
        "RISK_VLLM_IMAGE",
        "VLLM_UNIFIED_IMAGE_TO_DEPLOY",
        "AUDIO_VLLM_IMAGE_TO_DEPLOY",
    ):
        process_env.pop(key, None)
    return subprocess.run(
        ["bash", "-lc", command],
        cwd=ROOT,
        env=process_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_compose_services_consume_shared_vllm_image_authority() -> None:
    document = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    services = document["services"]

    for service in ("embedding-vllm", "embedding-ko-vllm", "risk-prompt-vllm"):
        assert services[service]["image"].startswith("${VLLM_IMAGE:")


def test_compose_context_does_not_materialize_retired_runtime_image_keys(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COMPOSE_PROJECT_NAME=test-project\n"
        "VLLM_IMAGE=registry.example.com/vllm@sha256:shared\n",
        encoding="utf-8",
    )
    result = _bash(
        f"source {shlex.quote(str(COMPOSE_CONTEXT))}; "
        f"PYTHON_BIN={shlex.quote(sys.executable)}; "
        f"ENV_FILE={shlex.quote(str(env_file))}; "
        f"COMPOSE_FILE={shlex.quote(str(COMPOSE_FILE))}; "
        "compose_context_init \"$PWD\"; "
        "printf '%s|%s' \"${EMBEDDING_KO_VLLM_IMAGE+x}\" \"${RISK_VLLM_IMAGE+x}\""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "|"


def test_remote_preflight_uses_one_shared_vllm_image_identity() -> None:
    deploy_env = DEPLOY_ENV.read_text(encoding="utf-8")
    remote_apply = APPLY_REMOTE_RELEASE.read_text(encoding="utf-8")

    assert "EMBEDDING_KO_VLLM_IMAGE_EFFECTIVE" not in deploy_env
    assert "RISK_VLLM_IMAGE_EFFECTIVE" not in deploy_env
    assert "EMBEDDING_KO_VLLM_IMAGE_EFFECTIVE" not in remote_apply
    assert "RISK_VLLM_IMAGE_EFFECTIVE" not in remote_apply
    assert remote_apply.count(
        'pull_required_runtime_image "shared vLLM" "${VLLM_IMAGE_EFFECTIVE}"'
    ) == 1
    assert "EMBEDDING_KO_VLLM_IMAGE_PROMOTION" not in remote_apply
    assert "RISK_VLLM_IMAGE_PROMOTION" not in remote_apply


def test_remote_promotion_updates_only_shared_and_profile_override_pins(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VLLM_IMAGE=registry.example.com/vllm@sha256:old\n"
        "AUDIO_VLLM_IMAGE=\n",
        encoding="utf-8",
    )
    result = _bash(
        f"source {shlex.quote(str(DEPLOY_ENV))}; "
        f"COMPOSE_ENV_FILE={shlex.quote(str(env_file))}; COMPOSE_EXPORTED_KEYS=(); "
        "VLLM_UNIFIED_IMAGE_TO_DEPLOY=registry.example.com/vllm@sha256:new; "
        "deploy_resolve_runtime_image_plan; deploy_apply_runtime_image_promotions; "
        f"cat {shlex.quote(str(env_file))}"
    )
    assert result.returncode == 0, result.stderr
    persisted = result.stdout.splitlines()
    assert "VLLM_IMAGE=registry.example.com/vllm@sha256:new" in persisted
    assert "AUDIO_VLLM_IMAGE=registry.example.com/vllm@sha256:new" in persisted
    assert not any(line.startswith("EMBEDDING_KO_VLLM_IMAGE=") for line in persisted)
    assert not any(line.startswith("RISK_VLLM_IMAGE=") for line in persisted)
