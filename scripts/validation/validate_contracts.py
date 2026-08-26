#!/usr/bin/env python3
"""정적 계약 검증을 실행하는 CLI 진입점.

실제 체크 목록은 scripts/validation/governance/cli.py의 CHECKS를 본다.
검증기는 프로덕션 패키지(src/)가 아니라 여기 scripts/ 아래 산다 -- 서비스 실행에는
필요 없고 런타임 이미지에 실려 나갈 이유도 없기 때문이다. 다만 검증 기준을
프로덕션 로직에서 그대로 가져다 쓰는 곳이 있어서 src/도 import path에 올린다.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / 'src')):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from scripts.validation.governance.cli import main  # noqa: E402


if __name__ == '__main__':
    main()
