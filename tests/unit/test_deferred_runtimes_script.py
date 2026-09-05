"""배포 프로필의 기본/명시 선택과 runtime-state projection을 검증한다."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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


def test_default_profile_defers_only_prompt_risk():
    result = run_script("--output", "json")

    payload = json.loads(result.stdout)
    assert payload == {
        "keys": ["risk_prompt"],
        "services": ["risk-prompt-vllm"],
        "profile": "retrieval_ready",
    }


def test_explicit_profile_overrides_default_profile():
    result = run_script("--profile", "main_only", "--output", "json")

    payload = json.loads(result.stdout)
    assert payload["keys"] == ["embedding", "embedding_ko", "risk_prompt"]
    assert payload["profile"] == "main_only"


def test_direct_runtime_list_overrides_profile():
    result = run_script(
        "--profile", "main_only", "--runtimes", "embedding", "--output", "json"
    )

    assert json.loads(result.stdout) == {
        "keys": ["embedding"],
        "services": ["embedding-vllm"],
        "profile": "",
    }


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
