"""VERSION 문자열이 실제로 박혀 있는 자리들의 단일 선언.

생성기(scripts/build/reset_version.py)와 검증기(scripts/validation/governance/
versioning.py)가 같은 표를 읽는다. 자리를 추가·삭제할 때는 여기만 고치며,
선언된 자리가 대상 파일에서 사라지면 생성과 검증이 함께 실패한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PLATFORM_IMAGE = 'ai-model-serving-platform:{version}'
UNIFIED_IMAGE = 'ai-model-serving-vllm-unified:{version}'


@dataclass(frozen=True)
class LineRef:
    """버전 문자열을 담고 있는 한 줄.

    pattern은 버전이 무엇이든 그 줄을 찾아내고, template은 주어진 버전에서 그 줄이
    어떤 모습이어야 하는지를 말한다. 생성기는 pattern을 찾아 template으로 바꾸고,
    검증기는 pattern이 찾은 줄들이 전부 template과 같은지 본다.
    """

    path: str
    pattern: str
    template: str
    #: True면 파일 안의 모든 매치를 대상으로 한다.
    all_occurrences: bool = False

    def expected(self, version: str, python_version: str) -> str:
        return self.template.format(version=version, python_version=python_version)

    def matches(self, text: str) -> list[str]:
        return re.findall(self.pattern, text)


LINE_REFS: tuple[LineRef, ...] = (
    LineRef('pyproject.toml', r'(?m)^version = ".+"$', 'version = "{python_version}"'),
    LineRef('specs/openapi.gateway.yaml', r'(?m)^  version: .+$', '  version: {version}'),
    LineRef('specs/openapi.risk-adapter.yaml', r'(?m)^  version: .+$', '  version: {version}'),
    LineRef(
        '.env.compose.example',
        r'(?m)^PLATFORM_IMAGE=ai-model-serving-platform:.+$',
        'PLATFORM_IMAGE=' + PLATFORM_IMAGE,
    ),
    LineRef(
        '.env.compose.example',
        r'(?m)^VLLM_IMAGE=ai-model-serving-vllm-unified:.+$',
        'VLLM_IMAGE=' + UNIFIED_IMAGE,
    ),
    LineRef(
        'configs/recommended_images.yaml',
        r'(?m)^    default: ai-model-serving-platform:.+$',
        '    default: ' + PLATFORM_IMAGE,
    ),
    LineRef(
        'configs/recommended_images.yaml',
        r'(?m)^    default: ai-model-serving-vllm-unified:.+$',
        '    default: ' + UNIFIED_IMAGE,
    ),
)

#: version_manifest.json에서 프로젝트 버전을 그대로 담는 필드들.
MANIFEST_VERSION_FIELDS = ('version', 'api_contract_version')
#: PEP 440 표기를 담는 필드.
MANIFEST_PYTHON_VERSION_FIELD = 'python_package_version'
#: image_tags 하위 필드 -> 이미지 이름 템플릿.
MANIFEST_IMAGE_TAGS = {
    'platform': PLATFORM_IMAGE,
    'risk_vllm': UNIFIED_IMAGE,
}

PROJECT_VERSION_PATTERN = r'\d+\.\d+\.\d+(-rc\.\d+)?'


def is_valid_project_version(version: str) -> bool:
    return bool(re.fullmatch(PROJECT_VERSION_PATTERN, version))


def python_package_version(version: str) -> str:
    """릴리스 버전을 PEP 440 파이썬 패키지 버전으로 바꾼다.

    Docker/이미지/문서는 0.1.0-rc.1 같은 SemVer 프리릴리스 표기를 쓰고,
    pyproject.toml은 같은 값의 PEP 440 표기(0.1.0rc1)를 써야 한다.
    """
    match = re.fullmatch(r'(\d+\.\d+\.\d+)-rc\.(\d+)', version)
    if match:
        return f'{match.group(1)}rc{match.group(2)}'
    return version
