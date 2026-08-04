#!/usr/bin/env python3
"""src/ai_model_serving/runtime_validation 패키지(라이브 런타임 검증 하네스)를
실행하는 CLI 진입점. `--config-only`로 부르면 서비스 기동 없이 registry
projection/리소스 정책만 검사한다(make validate가 이 모드로 사용)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.runtime_validation import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
