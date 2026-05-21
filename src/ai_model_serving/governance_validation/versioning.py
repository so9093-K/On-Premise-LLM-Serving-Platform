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
    FORBIDDEN_RESPONSE_FIELDS,
    REQUIRED_FILES,
    ROOT,
    iter_project_files,
    read_json,
    read_runtime_contract_text,
    read_yaml,
)

def python_package_version(version: str) -> str:
    match = re.fullmatch(r'(\d+\.\d+\.\d+)-rc\.(\d+)', version)
    if match:
        return f'{match.group(1)}rc{match.group(2)}'
    return version

def validate_project_version_format(version: str) -> None:
    if not re.fullmatch(r'\d+\.\d+\.\d+(-rc\.\d+)?', version):
        raise SystemExit(f'unsupported VERSION format: {version}; expected x.y.z or x.y.z-rc.n')

def validate_version_alignment() -> None:
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    validate_project_version_format(version)
    expected_pyproject_version = python_package_version(version)
    manifest = read_json('version_manifest.json')
    if manifest.get('version') != version:
        raise SystemExit('VERSION and version_manifest.json disagree')
    if manifest.get('python_package_version') != expected_pyproject_version:
        raise SystemExit('version_manifest.json python_package_version disagrees with VERSION')
    pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    if pyproject['project']['version'] != expected_pyproject_version:
        raise SystemExit('pyproject.toml project.version must use the PEP 440 spelling of VERSION')
    for path in ['specs/openapi.gateway.yaml', 'specs/openapi.risk-adapter.yaml']:
        doc = read_yaml(path)
        if doc['info']['version'] != version:
            raise SystemExit(f'{path} info.version disagrees with VERSION')

    version_refs = {
        'README.md': f'패키지 버전 | `{version}`',
        '.env.example': f'PROJECT_VERSION={version}',
        '.env.local.example': f'PROJECT_VERSION={version}',
        '.env.compose.example': f'PROJECT_VERSION={version}',
        'docs/release/versioning_policy.md': version,
    }
    for rel, expected in version_refs.items():
        if expected not in (ROOT / rel).read_text(encoding='utf-8'):
            raise SystemExit(f'{rel} is not aligned with VERSION {version}')
    compose_env = (ROOT / '.env.compose.example').read_text(encoding='utf-8')
    if f'PLATFORM_IMAGE=ai-model-serving-platform:{version}' not in compose_env:
        raise SystemExit('.env.compose.example PLATFORM_IMAGE is not aligned with VERSION')
    if f'RISK_VLLM_IMAGE=ai-model-serving-risk-vllm-kanana:{version}' not in compose_env:
        raise SystemExit('.env.compose.example RISK_VLLM_IMAGE is not aligned with VERSION')
    images = read_yaml('configs/recommended_images.yaml')['images']
    if images['platform']['default'] != f'ai-model-serving-platform:{version}':
        raise SystemExit('configs/recommended_images.yaml platform image is not aligned with VERSION')
    if images['risk_vllm']['default'] != f'ai-model-serving-risk-vllm-kanana:{version}':
        raise SystemExit('configs/recommended_images.yaml risk_vllm image is not aligned with VERSION')
    manifest_images = manifest.get('image_tags', {})
    if manifest_images.get('platform') != f'ai-model-serving-platform:{version}':
        raise SystemExit('version_manifest.json image_tags.platform is not aligned with VERSION')
    if manifest_images.get('risk_vllm') != f'ai-model-serving-risk-vllm-kanana:{version}':
        raise SystemExit('version_manifest.json image_tags.risk_vllm is not aligned with VERSION')

def validate_python_compatibility() -> None:
    py_version = (ROOT / '.python-version').read_text(encoding='utf-8').strip()
    if py_version != '3.12.13':
        raise SystemExit(f'.python-version must be 3.12.13, got {py_version}')

    if not ((3, 12) <= sys.version_info[:2] < (3, 15)):
        raise SystemExit(
            f'CPython >=3.12,<3.15 is required, got {sys.version.split()[0]}'
        )

    if os.getenv('STRICT_PYTHON_VERSION') == '1' and sys.version_info[:2] != (3, 12):
        raise SystemExit(
            f'CPython 3.12.x is required when STRICT_PYTHON_VERSION=1, '
            f'got {sys.version.split()[0]}'
        )

    pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    requires = pyproject['project']['requires-python']
    if requires != '>=3.12,<3.15':
        raise SystemExit(f'pyproject requires-python must be >=3.12,<3.15, got {requires}')
    if pyproject['tool']['ruff']['target-version'] != 'py312':
        raise SystemExit('ruff target-version must be py312')

    compat = read_yaml('configs/runtime_compatibility.yaml')
    if compat['python']['default_version'] != py_version:
        raise SystemExit('runtime_compatibility default Python version disagrees with .python-version')
    if compat['python']['supported_range'] != requires:
        raise SystemExit('runtime_compatibility supported_range disagrees with pyproject')

    env = (ROOT / '.env.example').read_text(encoding='utf-8')
    if 'PYTHON_VERSION=3.12.13' not in env:
        raise SystemExit('.env.example must include PYTHON_VERSION=3.12.13')

    doc = (ROOT / 'docs/development/python_compatibility.md').read_text(encoding='utf-8')
    if '3.12.13' not in doc or '>=3.12,<3.15' not in doc or '3.14' not in doc:
        raise SystemExit('Python compatibility doc must mention 3.12.13, >=3.12,<3.15, and 3.14')

def validate_version_bump_policy() -> None:
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    validate_project_version_format(version)
    manifest = read_json('version_manifest.json')
    if manifest.get('package_profile') != 'platform':
        raise SystemExit('manifest package_profile must be platform')
    if manifest.get('release_stage') not in ('pre-production release candidate', 'release'):
        raise SystemExit('manifest release_stage must be "release" or "pre-production release candidate"')
    policy = (ROOT / 'docs/release/versioning_policy.md').read_text(encoding='utf-8')
    pkg_version = python_package_version(version)
    required = list(dict.fromkeys([version, pkg_version, '운영 전', 'patch version 남발 금지', 'When not to bump VERSION']))
    for phrase in required:
        if phrase not in policy:
            raise SystemExit(f'versioning policy missing required phrase: {phrase}')
