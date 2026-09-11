"""저장소 .env를 스크립트의 process 환경으로 올린다.

production 코드의 ``settings_parts.env.load_dotenv``는 값을 ``DOTENV_VALUES``에
담아 settings가 읽게 한다. 반면 스크립트는 ``os.getenv``로 직접 읽으므로 환경에
올려야 한다. 목적이 달라 함수가 둘인 것이지, 어느 파일을 읽는지는 production이
소유한 ``default_env_path`` 하나로 유지한다.
"""
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
