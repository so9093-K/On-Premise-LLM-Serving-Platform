"""auth·exposure CLI가 공유하는 --env 경로 해석."""

from __future__ import annotations

from pathlib import Path

from ai_model_serving.project_paths import resolve_project_root

PROJECT_ROOT = resolve_project_root()


def resolve_env_path(value: str | None) -> Path | None:
    """``--env`` 인자를 절대 경로로 바꾼다. 값이 없으면 ``None``(기본 .env 사용).

    상대 경로는 canonical project root resolver가 결정한 root를 기준으로 해석한다.
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
