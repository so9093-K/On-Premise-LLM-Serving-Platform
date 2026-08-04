#!/usr/bin/env python3
"""실제 서비스와 GPU/vLLM 경계를 확인하는 runtime validation CLI 진입점."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.runtime_validation import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
