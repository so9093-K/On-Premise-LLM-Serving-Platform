from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    """필수 프로젝트 YAML을 읽고 최상위 값이 mapping인지 검증한다."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"cannot load configuration: {path}") from exc
    if not isinstance(document, dict):
        raise RuntimeError(f"configuration root must be a mapping: {path}")
    return document
