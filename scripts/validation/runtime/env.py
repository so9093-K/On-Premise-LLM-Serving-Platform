from __future__ import annotations

import os
from pathlib import Path

from ai_model_serving.settings_parts.dotenv_parser import load_strict_env_file
from ai_model_serving.settings_parts.env import default_env_path


def load_dotenv(root: Path) -> None:
    """프로젝트 표준 dotenv 문법으로 읽되, 이미 export된 값은 보존한다."""
    path = default_env_path(root)
    if not path.exists():
        return
    for key, value in load_strict_env_file(path).items():
        os.environ.setdefault(key, value)
