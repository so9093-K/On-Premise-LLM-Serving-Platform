from __future__ import annotations

import subprocess
import sys

from .common import ROOT


def validate_vllm_compose_contract() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/compose/validate_vllm_compose.py')],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise SystemExit(output or 'vLLM compose validation failed')
