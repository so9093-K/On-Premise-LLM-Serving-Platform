from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def python_package_version(version: str) -> str:
    """Convert the project release version to a PEP 440 Python package version.

    Docker/image/docs use SemVer-style prerelease tags such as 0.1.0-rc.1.
    pyproject.toml must use the equivalent PEP 440 spelling, e.g. 0.1.0rc1.
    """
    match = re.fullmatch(r'(\d+\.\d+\.\d+)-rc\.(\d+)', version)
    if match:
        return f'{match.group(1)}rc{match.group(2)}'
    return version


def is_valid_project_version(version: str) -> bool:
    return bool(re.fullmatch(r'\d+\.\d+\.\d+(-rc\.\d+)?', version))


def replace_openapi_version(path: Path, version: str) -> None:
    text = path.read_text(encoding='utf-8')
    text = re.sub(r'(?m)^  version: .+$', f'  version: {version}', text, count=1)
    path.write_text(text, encoding='utf-8')


def replace_pyproject_version(path: Path, version: str) -> None:
    text = path.read_text(encoding='utf-8')
    text = re.sub(r'(?m)^version = ".+"$', f'version = "{version}"', text, count=1)
    path.write_text(text, encoding='utf-8')



def replace_platform_image_tag(path: Path, version: str) -> None:
    text = path.read_text(encoding='utf-8')
    text = re.sub(
        r'(?m)^(\s*default:\s*ai-model-serving-platform:).+$',
        rf'\g<1>{version}',
        text,
        count=1,
    )
    path.write_text(text, encoding='utf-8')


def replace_vllm_unified_image_tag(path: Path, version: str) -> None:
    text = path.read_text(encoding='utf-8')
    # vllm/embedding_ko_vllm/risk_vllm 세 항목 모두 같은 unified 이미지를 가리키므로
    # count=0(전체 치환)으로 한 번에 갱신한다.
    text = re.sub(
        r'(?m)^(\s*default:\s*ai-model-serving-vllm-unified:).+$',
        rf'\g<1>{version}',
        text,
        count=0,
    )
    path.write_text(text, encoding='utf-8')


def replace_plain_version_references(path: Path, version: str) -> None:
    text = path.read_text(encoding='utf-8')
    text = re.sub(r'패키지 버전 \| `[^`]+`', f'패키지 버전 | `{version}`', text)
    text = re.sub(r'(?m)^PROJECT_VERSION=.+$', f'PROJECT_VERSION={version}', text)
    text = re.sub(
        r'(?m)^PLATFORM_IMAGE=ai-model-serving-platform:.+$',
        f'PLATFORM_IMAGE=ai-model-serving-platform:{version}',
        text,
    )
    text = re.sub(
        r'(?m)^VLLM_IMAGE=ai-model-serving-vllm-unified:.+$',
        f'VLLM_IMAGE=ai-model-serving-vllm-unified:{version}',
        text,
    )
    text = re.sub(
        r'(?m)^EMBEDDING_KO_VLLM_IMAGE=ai-model-serving-vllm-unified:.+$',
        f'EMBEDDING_KO_VLLM_IMAGE=ai-model-serving-vllm-unified:{version}',
        text,
    )
    text = re.sub(
        r'(?m)^RISK_VLLM_IMAGE=ai-model-serving-vllm-unified:.+$',
        f'RISK_VLLM_IMAGE=ai-model-serving-vllm-unified:{version}',
        text,
    )
    text = re.sub(r'(?m)^version: .+$', f'version: {version}', text, count=1)
    text = re.sub(
        r'(?s)(## 1\. Current package version\n\n```text\n).*?(\n```)',
        rf'\g<1>{version}\g<2>',
        text,
        count=1,
    )
    path.write_text(text, encoding='utf-8')


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit('usage: reset_version.py <version>')
    version = sys.argv[1].strip()
    if not is_valid_project_version(version):
        raise SystemExit(f'invalid project version: {version}; expected x.y.z or x.y.z-rc.n')

    (ROOT / 'VERSION').write_text(version + '\n', encoding='utf-8')

    manifest_path = ROOT / 'version_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    manifest['version'] = version
    manifest['python_package_version'] = python_package_version(version)
    manifest['api_contract_version'] = version
    if 'image_tags' not in manifest:
        manifest['image_tags'] = {}
    manifest['image_tags']['platform'] = f'ai-model-serving-platform:{version}'
    manifest['image_tags']['risk_vllm'] = f'ai-model-serving-vllm-unified:{version}'
    manifest['version_reset'] = True
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    for path in [ROOT / 'specs/openapi.gateway.yaml', ROOT / 'specs/openapi.risk-adapter.yaml']:
        replace_openapi_version(path, version)

    replace_pyproject_version(ROOT / 'pyproject.toml', python_package_version(version))
    for path in [
        ROOT / 'README.md',
        ROOT / '.env.example',
        ROOT / '.env.local.example',
        ROOT / '.env.compose.example',
        ROOT / 'configs/runtime_compatibility.yaml',
        ROOT / 'docs/release/versioning_policy.md',
    ]:
        if path.exists():
            replace_plain_version_references(path, version)

    image_config = ROOT / 'configs/recommended_images.yaml'
    if image_config.exists():
        replace_platform_image_tag(image_config, version)
        replace_vllm_unified_image_tag(image_config, version)

    print(f'version reset to {version}')


if __name__ == '__main__':
    main()
