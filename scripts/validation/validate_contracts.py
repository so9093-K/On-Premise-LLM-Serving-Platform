"""governance_validation 패키지의 계약 검증을 실행하는 CLI 진입점.
실제 체크 목록은 src/ai_model_serving/governance_validation/cli.py의 CHECKS를 본다."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from ai_model_serving.governance_validation.cli import main


if __name__ == '__main__':
    main()
