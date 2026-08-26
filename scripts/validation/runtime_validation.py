#!/usr/bin/env python3
"""실제 서비스와 GPU/vLLM 경계를 확인하는 runtime validation CLI 진입점.

체크 구현은 scripts/validation/runtime/ 아래에 있다. 검증기는 프로덕션
패키지(src/)가 아니라 scripts/ 아래 사는데, 살아있는 스택을 밖에서 찔러보는
도구라 서비스 자신이 품고 있을 이유가 없기 때문이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from scripts.validation.runtime import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
