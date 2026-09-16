from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_CONTEXT = ROOT / "scripts" / "lib" / "compose_context.sh"
DEPLOY_ENV = ROOT / "scripts" / "lib" / "deploy_env.sh"
COMPOSE_FILE = ROOT / "ops" / "compose" / "full-stack.private-network.yaml"

IMAGE_ENV_KEYS = (
    "VLLM_IMAGE",
    "EMBEDDING_KO_VLLM_IMAGE",
    "RISK_VLLM_IMAGE",
    "VLLM_UNIFIED_IMAGE_TO_DEPLOY",
    "RISK_VLLM_IMAGE_TO_DEPLOY",
    "AUDIO_VLLM_IMAGE_TO_DEPLOY",
)


def _bash(command: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process_env = dict(os.environ)
    # These tests exercise env-file and explicit per-call precedence. CI may set
    # deployment image variables globally, so remove them before constructing the
    # test process environment; individual tests can add process overrides via env.
    for key in IMAGE_ENV_KEYS:
        process_env.pop(key, None)
    if env:
        process_env.update(env)
    return subprocess.run(
        ["bash", "-lc", command],
        cwd=ROOT,
        env=process_env,
        text=True,
        capture_output=True,
        check=True,
    )


def test_compose_context_projects_shared_image_when_legacy_keys_are_absent(tmp_path: Path) -> None:
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
        "printf '%s|%s' \"$EMBEDDING_KO_VLLM_IMAGE\" \"$RISK_VLLM_IMAGE\""
    )
    assert result.stdout == (
        "registry.example.com/vllm@sha256:shared|"
        "registry.example.com/vllm@sha256:shared"
    )


def test_compose_context_preserves_existing_legacy_overrides(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VLLM_IMAGE=registry.example.com/vllm@sha256:shared\n"
        "EMBEDDING_KO_VLLM_IMAGE=registry.example.com/vllm@sha256:ko\n"
        "RISK_VLLM_IMAGE=registry.example.com/vllm@sha256:risk\n",
        encoding="utf-8",
    )
    result = _bash(
        f"source {shlex.quote(str(COMPOSE_CONTEXT))}; "
        f"PYTHON_BIN={shlex.quote(sys.executable)}; "
        f"ENV_FILE={shlex.quote(str(env_file))}; "
        f"COMPOSE_FILE={shlex.quote(str(COMPOSE_FILE))}; "
        "compose_context_init \"$PWD\"; "
        "printf '%s|%s' \"$EMBEDDING_KO_VLLM_IMAGE\" \"$RISK_VLLM_IMAGE\""
    )
    assert result.stdout == (
        "registry.example.com/vllm@sha256:ko|"
        "registry.example.com/vllm@sha256:risk"
    )


def test_remote_image_plan_inherits_shared_authority_without_persisting_legacy_keys(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VLLM_IMAGE=registry.example.com/vllm@sha256:old\n"
        "AUDIO_VLLM_IMAGE=\n",
        encoding="utf-8",
    )
    result = _bash(
        f"source {shlex.quote(str(DEPLOY_ENV))}; "
        f"COMPOSE_ENV_FILE={shlex.quote(str(env_file))}; COMPOSE_EXPORTED_KEYS=(); "
        "deploy_resolve_runtime_image_plan; "
        "printf '%s|%s\\n' \"$EMBEDDING_KO_VLLM_IMAGE_EFFECTIVE\" \"$RISK_VLLM_IMAGE_EFFECTIVE\"; "
        "VLLM_UNIFIED_IMAGE_TO_DEPLOY=registry.example.com/vllm@sha256:new; "
        "deploy_resolve_runtime_image_plan; deploy_apply_runtime_image_promotions; "
        f"cat {shlex.quote(str(env_file))}"
    )
    lines = result.stdout.splitlines()
    assert lines[0] == (
        "registry.example.com/vllm@sha256:old|"
        "registry.example.com/vllm@sha256:old"
    )
    persisted = lines[1:]
    assert "VLLM_IMAGE=registry.example.com/vllm@sha256:new" in persisted
    assert "AUDIO_VLLM_IMAGE=registry.example.com/vllm@sha256:new" in persisted
    assert not any(line.startswith("EMBEDDING_KO_VLLM_IMAGE=") for line in persisted)
    assert not any(line.startswith("RISK_VLLM_IMAGE=") for line in persisted)


def test_remote_compose_export_projects_shared_image_without_mutating_env(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    original = "VLLM_IMAGE=registry.example.com/vllm@sha256:shared\n"
    env_file.write_text(original, encoding="utf-8")
    result = _bash(
        f"source {shlex.quote(str(DEPLOY_ENV))}; "
        f"COMPOSE_ENV_FILE={shlex.quote(str(env_file))}; COMPOSE_EXPORTED_KEYS=(); "
        "deploy_export_compose_env; "
        "printf '%s|%s' \"$EMBEDDING_KO_VLLM_IMAGE\" \"$RISK_VLLM_IMAGE\""
    )
    assert result.stdout == (
        "registry.example.com/vllm@sha256:shared|"
        "registry.example.com/vllm@sha256:shared"
    )
    assert env_file.read_text(encoding="utf-8") == original
