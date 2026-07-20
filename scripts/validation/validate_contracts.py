from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

# release-hygiene 테스트를 위한 호환성 마커: validate_vllm_compose.py
from ai_model_serving.governance_validation.cli import main


if __name__ == '__main__':
    main()
