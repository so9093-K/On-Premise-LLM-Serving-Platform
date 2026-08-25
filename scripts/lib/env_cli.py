"""scripts/env CLI가 공유하는 경로 해석과 오류 출력."""

from __future__ import annotations

import sys
from pathlib import Path

from ai_model_serving.settings import ROOT as PROJECT_ROOT


def resolve_path(value: str) -> Path:
    """상대 경로를 프로젝트 루트 기준 절대 경로로 바꾼다."""
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def print_env_error(path: Path, message: str) -> None:
    """strict env 파싱 실패를 사람이 고칠 수 있는 형태로 출력한다."""
    print(f"[env] invalid env file: {path}", file=sys.stderr)
    print("", file=sys.stderr)
    for line in message.splitlines():
        print(f"  {line}", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "Fix: keep strict KEY=VALUE lines with no duplicates, quotes, inline comments, or export syntax.",
        file=sys.stderr,
    )
