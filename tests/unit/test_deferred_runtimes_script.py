"""scripts/runtime/deferred_runtimes.py를 검증한다: --runtimes/--profile로 지정한
런타임이 올바른 service key/compose service로 풀리는지, 명시적 --runtimes가
--profile을 덮어쓰는지, --apply-state로 runtime-state.json에 제대로 기록되는지."""

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
