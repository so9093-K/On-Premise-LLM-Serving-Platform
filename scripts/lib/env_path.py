"""auth·exposure CLI가 공유하는 --env 경로 해석."""

from __future__ import annotations

from pathlib import Path

from ai_model_serving.settings import ROOT as PROJECT_ROOT


def resolve_env_path(value: str | None) -> Path | None:
    """``--env`` 인자를 절대 경로로 바꾼다. 값이 없으면 ``None``(기본 .env 사용).

    상대 경로의 기준은 프로젝트가 스스로 계산한 루트다. 이전에는 스크립트마다
    ``parents[2]``로 다시 구하거나 이 함수를 통째로 복사해 두어서, 같은 이름의
    helper가 7벌 있고 그중 셋은 동작이 서로 달랐다(None 처리 유무, 기준 루트).
    """
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_env_values(path: Path) -> dict[str, str]:
    """기존 env 파일의 KEY=VALUE를 읽는다. 파일이 없으면 빈 dict.

    auth/exposure plan이 "지금 값"과 "목표 값"을 비교할 때 쓰는 유일한 읽기 경로다.
    """
    from scripts.config.setup_env import parse_env_template

    if not path.exists():
        return {}
    _, values = parse_env_template(path)
    return values
