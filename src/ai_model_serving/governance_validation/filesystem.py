from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit('PyYAML is required: pip install pyyaml') from exc

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:
    raise SystemExit('jsonschema is required: pip install jsonschema') from exc

from .common import (
    EXPECTED_PORTS,
    FORBIDDEN_RESPONSE_FIELDS,
    REQUIRED_FILES,
    ROOT,
    iter_project_files,
    read_json,
    read_runtime_contract_text,
    read_yaml,
)

def validate_json_and_yaml_parse() -> None:
    for path in iter_project_files('*.json'):
        json.loads(path.read_text(encoding='utf-8'))
    for path in iter_project_files('*.yaml'):
        yaml.safe_load(path.read_text(encoding='utf-8'))
    for path in iter_project_files('*.yml'):
        yaml.safe_load(path.read_text(encoding='utf-8'))

def validate_required_files() -> None:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).exists()]
    if missing:
        raise SystemExit(f'missing required files: {missing}')
