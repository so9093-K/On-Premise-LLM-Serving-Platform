#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.runtime_validation import (  # noqa: E402
    FORBIDDEN_RISK_FIELDS,
    main,
    render_vllm_command,
)

# Compatibility markers for static release governance:
# reports/runtime
# nvidia-smi
# /api/v1/targets
# FORBIDDEN_RISK_FIELDS
# Raw prompts, user text, and model output are not written
# --config-only

__all__ = ["ROOT", "FORBIDDEN_RISK_FIELDS", "main", "render_vllm_command"]

if __name__ == "__main__":
    raise SystemExit(main())
