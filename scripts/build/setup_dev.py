#!/usr/bin/env python3
"""Prepare the Platform development environment from the shared lock."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build.setup_python_environment import main as setup_environment  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(setup_environment(["--profile", "development"]))
