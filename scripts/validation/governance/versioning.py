from __future__ import annotations

import re
import tomllib

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from scripts.build.check_python import SUPPORTED_LABEL, SUPPORTED_SPECIFIER, is_supported

from .common import (
    ROOT,
    read_json,
)

# 이 모듈이 scripts.validation.governance.versioning 으로 import됐다는 것 자체가
# 저장소 루트가 이미 import path에 있다는 뜻이라, 여기서 sys.path를 손댈 필요는 없다.
from scripts.lib.version_refs import (
    LINE_REFS,
    MANIFEST_IMAGE_TAGS,
    MANIFEST_PYTHON_VERSION_FIELD,
    MANIFEST_VERSION_FIELDS,
    is_valid_project_version,
    python_package_version,
)


def validate_version_alignment() -> None:
    """VERSION이 박혀 있는 모든 자리가 실제로 그 값인지 확인한다.

    자리 목록은 scripts/lib/version_refs.py에 있고, 생성기(reset_version.py)도 같은
    표를 쓴다. 예전엔 생성기와 검증기가 목록을 따로 들고 있어서 이미 갈라져 있었다
    -- 생성기만 갱신하고 검증기는 보지 않는 자리가 있었다.
    """
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    if not is_valid_project_version(version):
        raise SystemExit(f'unsupported VERSION format: {version}; expected x.y.z or x.y.z-rc.n')
    py_version = python_package_version(version)

    failures: list[str] = []

    manifest = read_json('version_manifest.json')
    for field in MANIFEST_VERSION_FIELDS:
        if manifest.get(field) != version:
            failures.append(f'version_manifest.json {field}={manifest.get(field)!r}, expected {version!r}')
    if manifest.get(MANIFEST_PYTHON_VERSION_FIELD) != py_version:
        failures.append(
            f'version_manifest.json {MANIFEST_PYTHON_VERSION_FIELD}='
            f'{manifest.get(MANIFEST_PYTHON_VERSION_FIELD)!r}, expected {py_version!r}'
        )
    image_tags = manifest.get('image_tags', {})
    for field, template in MANIFEST_IMAGE_TAGS.items():
        expected = template.format(version=version)
        if image_tags.get(field) != expected:
            failures.append(
                f'version_manifest.json image_tags.{field}={image_tags.get(field)!r}, expected {expected!r}'
            )

    for ref in LINE_REFS:
        path = ROOT / ref.path
        if not path.exists():
            failures.append(f'{ref.path}: declared as a version reference but the file is missing')
            continue
        found = ref.matches(path.read_text(encoding='utf-8'))
        expected = ref.expected(version, py_version)
        if not found:
            # 표에 선언된 자리가 사라졌다. 통과시키면 그 파일은 검증 없이 흘러간다.
            failures.append(f'{ref.path}: no line matched {ref.pattern!r} (version reference disappeared)')
            continue
        # findall은 그룹이 있으면 그룹만 돌려주므로, 여기 pattern들은 그룹을 쓰지 않는다.
        stale = [line for line in found if line != expected]
        if stale:
            failures.append(f'{ref.path}: {stale} != expected {expected!r}')

    # pyproject는 줄 형태만이 아니라 파싱 결과로도 맞아야 한다 -- 빌드가 실제로
    # 읽는 값은 이쪽이다.
    pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    if pyproject['project']['version'] != py_version:
        failures.append(
            f"pyproject.toml project.version={pyproject['project']['version']!r}, expected {py_version!r}"
        )

    if failures:
        raise SystemExit(
            f'VERSION {version} is not propagated consistently:\n  ' + '\n  '.join(failures)
        )


def validate_python_compatibility() -> None:
    """Python 지원 범위와 Linux 운영 기준 patch/image를 확인한다.

    실행 중인 interpreter 자체는 여기서 보지 않는다 -- scripts/build/check_python.py가
    validate/test/start/package 등 모든 진입점에서 먼저 그걸 검사하므로, 여기서 또
    검사하면 같은 정책이 두 곳에 살면서 갈라진다.

    GitHub Actions는 .python-version에서 portable minor를 직접 계산한다. 실행
    workflow는 provider가 검증하므로 repository 공통 계약에서 다시 해석하지 않는다.
    """
    py_version = (ROOT / '.python-version').read_text(encoding='utf-8').strip()
    match = re.fullmatch(r'(\d+)\.(\d+)\.\d+', py_version)
    if not match or not is_supported((int(match.group(1)), int(match.group(2)))):
        raise SystemExit(f'.python-version must be a {SUPPORTED_LABEL} patch release, got {py_version!r}')
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    declared = project.get('project', {}).get('requires-python')
    if declared != SUPPORTED_SPECIFIER:
        raise SystemExit(
            'pyproject.toml project.requires-python must match the bootstrap policy '
            f'{SUPPORTED_SPECIFIER!r}, got {declared!r}'
        )

    # .python-version은 Linux application/CI image의 exact patch SoT다. Dockerfile과
    # GitLab template에 digest 전체를 중복 기록할 수밖에 없지만, 둘이 같은 ref인지와
    # patch가 기준값을 따르는지는 이 단일 gate에서 확인한다.
    docker_match = re.search(
        r'(?m)^FROM\s+(python:[^\s]+)$',
        (ROOT / 'Dockerfile').read_text(encoding='utf-8'),
    )
    gitlab_match = re.search(
        r'(?m)^\s+image:\s+(python:[^\s]+)$',
        (ROOT / '.gitlab-ci.yml').read_text(encoding='utf-8'),
    )
    expected_image = re.compile(
        rf'^python:{re.escape(py_version)}-slim@sha256:[0-9a-f]{{64}}$'
    )
    if docker_match is None or not expected_image.fullmatch(docker_match.group(1)):
        actual = docker_match.group(1) if docker_match else None
        raise SystemExit(
            f'Dockerfile base image must use python:{py_version}-slim with a sha256 digest, '
            f'got {actual!r}'
        )
    if gitlab_match is None or not expected_image.fullmatch(gitlab_match.group(1)):
        actual = gitlab_match.group(1) if gitlab_match else None
        raise SystemExit(
            f'.gitlab-ci.yml Python image must use python:{py_version}-slim with a sha256 digest, '
            f'got {actual!r}'
        )
    if docker_match.group(1) != gitlab_match.group(1):
        raise SystemExit(
            'Dockerfile and .gitlab-ci.yml must use the same digest-pinned Python image'
        )


def _read_lock_pins(filename: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in (ROOT / filename).read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement as exc:
            raise SystemExit(f'{filename}: invalid requirement {line!r}: {exc}') from exc
        specifiers = list(requirement.specifier)
        if len(specifiers) != 1 or specifiers[0].operator != '==' or '*' in specifiers[0].version:
            raise SystemExit(f'{filename}: lock entry must be one exact pin: {line!r}')
        name = canonicalize_name(requirement.name)
        if name in pins:
            raise SystemExit(f'{filename}: duplicate pin for {name}')
        pins[name] = specifiers[0].version
    return pins


def validate_dependency_locks() -> None:
    """Lock files are static repository contracts, not runtime test cases."""
    runtime = _read_lock_pins('requirements.runtime.lock')
    development = _read_lock_pins('requirements.lock')
    failures = [
        f'runtime/development lock drift: {name} {version!r} != {development.get(name)!r}'
        for name, version in runtime.items()
        if development.get(name) != version
    ]

    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    declared_by_lock = (
        ('requirements.runtime.lock', runtime, project['dependencies']),
        (
            'requirements.lock',
            development,
            project['dependencies'] + project['optional-dependencies']['contract'],
        ),
    )
    for filename, pins, declarations in declared_by_lock:
        for declaration in declarations:
            requirement = Requirement(declaration)
            name = canonicalize_name(requirement.name)
            version = pins.get(name)
            if version is None or not requirement.specifier.contains(version):
                failures.append(f'{filename}: does not satisfy {declaration!r}')

    if failures:
        raise SystemExit('\n  '.join(failures))
