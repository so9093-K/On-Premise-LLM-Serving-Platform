from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_primary_cli_help_uses_korean_argparse_labels() -> None:
    for rel in ["scripts/auth/auth_status.py", "scripts/auth/auth_doctor.py", "scripts/config/setup_env.py", "scripts/models/modelctl.py"]:
        result = subprocess.run([sys.executable, rel, "--help"], cwd=ROOT, check=True, text=True, capture_output=True, timeout=10)
        assert "사용법:" in result.stdout, rel
        assert "옵션:" in result.stdout, rel
        assert "show this help message and exit" not in result.stdout, rel
