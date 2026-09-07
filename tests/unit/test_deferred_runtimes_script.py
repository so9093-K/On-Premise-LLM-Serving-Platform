"""배포 프로필의 기본 선택과 명시적 override 우선순위를 검증한다."""

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


def test_default_profile_defers_all_secondary_runtimes():
    result = run_script("--output", "json")

    payload = json.loads(result.stdout)
    assert payload == {
        "keys": ["embedding", "embedding_ko", "risk_prompt"],
        "services": ["embedding-vllm", "embedding-ko-vllm", "risk-prompt-vllm"],
        "profile": "main_only",
    }


def test_explicit_profile_overrides_default_profile():
    result = run_script("--profile", "retrieval_ready", "--output", "json")

    payload = json.loads(result.stdout)
    assert payload["keys"] == ["risk_prompt"]
    assert payload["profile"] == "retrieval_ready"


def test_direct_runtime_list_overrides_profile():
    result = run_script(
        "--profile", "main_only", "--runtimes", "embedding", "--output", "json"
    )

    assert json.loads(result.stdout) == {
        "keys": ["embedding"],
        "services": ["embedding-vllm"],
        "profile": "",
    }
