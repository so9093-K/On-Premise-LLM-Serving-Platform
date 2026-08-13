from __future__ import annotations

import json

try:
    import yaml
except ImportError as exc:
    raise SystemExit('PyYAML is required: pip install pyyaml') from exc

from .common import (
    iter_project_files,
)

def validate_json_and_yaml_parse() -> None:
    for path in iter_project_files('*.json'):
        json.loads(path.read_text(encoding='utf-8'))
    for path in iter_project_files('*.yaml'):
        yaml.safe_load(path.read_text(encoding='utf-8'))
    for path in iter_project_files('*.yml'):
        yaml.safe_load(path.read_text(encoding='utf-8'))
