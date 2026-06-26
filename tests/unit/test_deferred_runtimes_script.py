from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/runtime/deferred_runtimes.py"


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--config-root", str(ROOT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )


def test_deferred_runtime_script_resolves_keys_and_services():
    result = run_script("--runtimes", "embedding,embedding-ko-vllm", "--output", "json")

    payload = json.loads(result.stdout)
    assert payload["keys"] == ["embedding", "embedding_ko"]
    assert payload["services"] == ["embedding-vllm", "embedding-ko-vllm"]


def test_deferred_runtime_script_resolves_deploy_profile():
    result = run_script("--profile", "main_only", "--output", "json")

    payload = json.loads(result.stdout)
    assert payload["profile"] == "main_only"
    assert payload["keys"] == ["embedding", "embedding_ko", "risk_prompt"]
    assert payload["services"] == ["embedding-vllm", "embedding-ko-vllm", "risk-prompt-vllm"]


def test_deferred_runtime_script_explicit_runtimes_override_profile():
    result = run_script(
        "--profile",
        "main_only",
        "--runtimes",
        "risk_prompt",
        "--output",
        "json",
    )

    payload = json.loads(result.stdout)
    assert payload["keys"] == ["risk_prompt"]
    assert payload["services"] == ["risk-prompt-vllm"]


def test_deploy_profiles_only_reference_known_runtimes():
    profiles = yaml.safe_load((ROOT / "configs/deploy_profiles.yaml").read_text(encoding="utf-8"))

    for profile in profiles["profiles"]:
        run_script("--profile", profile, "--output", "json")


def test_deferred_runtime_script_applies_state_metadata(tmp_path):
    state_path = tmp_path / "runtime-state.json"

    run_script(
        "--runtimes",
        "risk_prompt",
        "--state-path",
        str(state_path),
        "--apply-state",
        "--reason",
        "deferred_at_deploy",
        "--source",
        "deploy",
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    record = payload["states"]["risk_prompt"]
    assert record["state"] == "stopped"
    assert record["reason"] == "deferred_at_deploy"
    assert record["source"] == "deploy"
    assert isinstance(record["updated_at"], float)
