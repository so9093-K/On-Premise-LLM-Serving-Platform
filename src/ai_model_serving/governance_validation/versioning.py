from __future__ import annotations

import re
import tomllib

from .common import (
    ROOT,
    read_json,
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
        '.env.example': f'PROJECT_VERSION={version}',
        '.env.local.example': f'PROJECT_VERSION={version}',
        '.env.compose.example': f'PROJECT_VERSION={version}',
    }
    for rel, expected in version_refs.items():
        if expected not in (ROOT / rel).read_text(encoding='utf-8'):
            raise SystemExit(f'{rel} is not aligned with VERSION {version}')
    compose_env = (ROOT / '.env.compose.example').read_text(encoding='utf-8')
    if f'PLATFORM_IMAGE=ai-model-serving-platform:{version}' not in compose_env:
        raise SystemExit('.env.compose.example PLATFORM_IMAGE is not aligned with VERSION')
    if f'RISK_VLLM_IMAGE=ai-model-serving-vllm-unified:{version}' not in compose_env:
        raise SystemExit('.env.compose.example RISK_VLLM_IMAGE is not aligned with VERSION')
    images = read_yaml('configs/recommended_images.yaml')['images']
    if images['platform']['default'] != f'ai-model-serving-platform:{version}':
        raise SystemExit('configs/recommended_images.yaml platform image is not aligned with VERSION')
    for key in ('vllm', 'embedding_ko_vllm', 'risk_vllm'):
        if images[key]['default'] != f'ai-model-serving-vllm-unified:{version}':
            raise SystemExit(f'configs/recommended_images.yaml {key} image is not aligned with VERSION')
    manifest_images = manifest.get('image_tags', {})
    if manifest_images.get('platform') != f'ai-model-serving-platform:{version}':
        raise SystemExit('version_manifest.json image_tags.platform is not aligned with VERSION')
    if manifest_images.get('risk_vllm') != f'ai-model-serving-vllm-unified:{version}':
        raise SystemExit('version_manifest.json image_tags.risk_vllm is not aligned with VERSION')

def validate_python_compatibility() -> None:
    """`.python-version`이 지원 범위 안의 patch release인지 확인한다.

    실행 중인 interpreter 자체는 여기서 보지 않는다 -- scripts/build/check_python.py가
    validate/test/start/package 등 모든 진입점에서 먼저 그걸 검사하므로, 여기서 또
    검사하면 같은 정책이 두 곳에 살면서 갈라진다.
    """
    py_version = (ROOT / '.python-version').read_text(encoding='utf-8').strip()
    match = re.fullmatch(r'(\d+)\.(\d+)\.\d+', py_version)
    if not match or not ((3, 12) <= (int(match.group(1)), int(match.group(2))) < (3, 15)):
        raise SystemExit(f'.python-version must be a >=3.12,<3.15 patch release, got {py_version!r}')
