#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
VERSION="$(cat "$ROOT/VERSION")"
PACKAGE_NAME="${PACKAGE_NAME:-ai_model_serving_platform}"
PACKAGE_ROOT="${PACKAGE_ROOT:-ai_model_serving_platform}"
DIST="${PACKAGE_DIST:-$ROOT/dist}"
OUT="$DIST/${PACKAGE_NAME}_${VERSION}.zip"
TMP_OUT="$DIST/.${PACKAGE_NAME}_${VERSION}.zip.tmp.$$"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/ai-model-serving-package.XXXXXX")"

cleanup() {
  rm -rf "$STAGE"
  rm -f "$TMP_OUT"
}
trap cleanup EXIT

mkdir -p "$DIST"

if [[ "${PACKAGE_SKIP_VALIDATION:-0}" != "1" ]]; then
  PYTHON_BIN="$PYTHON_BIN" bash "$ROOT/scripts/validation/run_validate.sh"
fi

cd "$ROOT"
source scripts/lib/source_provenance.sh
read_source_provenance

"$PYTHON_BIN" - "$ROOT" "$STAGE/$PACKAGE_ROOT" <<'PYCODE'
from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

sys.dont_write_bytecode = True

src = Path(sys.argv[1]).resolve()
dst = Path(sys.argv[2]).resolve()

exclude_tree_dirs = {
    '.agents',
    '.claude',
    '.codex',
    '.cursor',
    '.git',
    '.cache',
    '.mypy_cache',
    '.pytest_cache',
    '.ruff_cache',
    '.runtime',
    '.tox',
    '.venv',
    'venv',
    '__pycache__',
    'model_cache',
}
exclude_top_level_dirs = {
    '.github',
    '.other',
    'build',
    'dist',
    'env',
    'logs',
    'model_cache',
    'models',
    'outputs',
    'run',
}
# tests는 포함한다. 자동화와 배포 전 make check가 같은 source의 테스트를 실행할 수
# 있어야 하므로, 테스트가 빠진 ZIP은 검증 입력이 불완전하다.
#
# 예전 주석이 든 배제 근거는 재보니 셋 다 성립하지 않았다.
#   크기        압축 후 111KB. 전체 ZIP 2.8MB 대비 +4%.
#   공격 표면    앱이 import하지 않고, .dockerignore가 막아 컨테이너에도 안 들어간다.
#   보증 불명확  오히려 반대다. 같은 ZIP에 실린 테스트가 곧 그 버전을 통과시킨
#                테스트이고, 빠져 있으면 받는 쪽이 검증 자체를 못 한다.
exclude_suffixes = (
    '.pyc',
    '.pyo',
    '.secret',
    '.pem',
    '.key',
)
exclude_file_patterns = (
    '.env',
    '.env.*',
)
env_contract = yaml.safe_load(
    (src / 'configs' / 'env_contract.yaml').read_text(encoding='utf-8')
)
if not isinstance(env_contract, dict):
    raise SystemExit('configs/env_contract.yaml must contain a mapping')
env_examples = env_contract.get('env_examples')
safe_env_examples = set(env_examples) if isinstance(env_examples, dict) else set()
if not safe_env_examples:
    raise SystemExit('configs/env_contract.yaml must declare env_examples for packaging')


def skip_dir(rel_parts: tuple[str, ...], name: str) -> bool:
    top = rel_parts[0] if rel_parts else name
    return (
        rel_parts[:2] == ('reports', 'runtime')
        or
        name.endswith('.egg-info')
        or top in exclude_top_level_dirs
        or name in exclude_tree_dirs
        or any(part in exclude_tree_dirs or part.endswith('.egg-info') for part in rel_parts)
    )


def skip_file(rel_parts: tuple[str, ...], name: str) -> bool:
    top = rel_parts[0] if rel_parts else name
    if top in exclude_top_level_dirs:
        return True
    if any(part in exclude_tree_dirs or part.endswith('.egg-info') for part in rel_parts[:-1]):
        return True
    # Runtime validation reports are host-specific generated evidence. They are
    # gitignored, so including any of them would make the release ZIP depend on
    # the packager's local state and differ from a clean CI checkout.
    if len(rel_parts) >= 2 and rel_parts[0] == 'reports' and rel_parts[1] == 'runtime':
        return True
    if name in safe_env_examples:
        return False
    if name.endswith(exclude_suffixes):
        return True
    if any(fnmatch.fnmatch(name, pattern) for pattern in exclude_file_patterns):
        return True
    return False


if dst.exists():
    shutil.rmtree(dst)
dst.mkdir(parents=True)

try:
    # -s는 경로마다 Git이 기록한 mode를 함께 준다. ZIP에 담는 mode를 working tree의
    # st_mode에서 읽으면 packager의 umask가 산출물에 새어 들어간다 -- 같은 commit이
    # 664/775로 체크아웃된 곳과 644/755로 체크아웃된 곳에서 서로 다른 ZIP이 나온다.
    # Git은 실행 비트만 기록하므로 그 두 값이 release의 기준이다.
    tracked_output = subprocess.check_output(
        ['git', '-C', str(src), 'ls-files', '-sz'],
        stderr=subprocess.PIPE,
    )
except (FileNotFoundError, subprocess.CalledProcessError) as exc:
    raise SystemExit('make package requires a Git working tree to select tracked inputs') from exc

CANONICAL_MODES = {'100755': 0o755, '100644': 0o644}
tracked_modes: dict[str, int] = {}
for entry in filter(None, tracked_output.decode('utf-8').split('\0')):
    metadata, _, entry_path = entry.partition('\t')
    git_mode = metadata.split(' ', 1)[0]
    if git_mode not in CANONICAL_MODES:
        # gitlink(160000)나 symlink(120000)는 이 release가 담는 대상이 아니다.
        raise SystemExit(f'unsupported git mode {git_mode} for {entry_path!r}')
    tracked_modes[entry_path] = CANONICAL_MODES[git_mode]

for raw_path in sorted(tracked_modes):
    rel_path = Path(raw_path)
    rel_parts = rel_path.parts
    if rel_path.is_absolute() or '..' in rel_parts:
        raise SystemExit(f'unsafe tracked path: {raw_path!r}')
    if any(
        skip_dir(tuple(rel_parts[: index + 1]), dirname)
        for index, dirname in enumerate(rel_parts[:-1])
    ):
        continue
    if skip_file(rel_parts, rel_path.name):
        continue
    source_file = src / rel_path
    if not source_file.is_file():
        # git ls-files에는 working tree에서 아직 stage하지 않은 삭제도 남는다.
        # package는 현재 working tree의 tracked 파일을 담으므로 그 삭제를 존중한다.
        continue
    target_file = dst / rel_path
    target_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_file, target_file)
    # staging tree 자체를 정규화해 둔다. ZIP writer는 이 mode를 그대로 기록한다.
    target_file.chmod(tracked_modes[raw_path])

PYCODE

# 어느 소스에서 나온 ZIP인지 함께 담는다. image는 OCI label로 같은 값을 싣는다.
# 빌드 시각은 일부러 넣지 않는다 -- 같은 commit에서 같은 bytes가 나와야 한다.
SOURCE_REVISION="$SOURCE_REVISION" SOURCE_STATE="$SOURCE_STATE" VERSION="$VERSION" \
  "$PYTHON_BIN" - "$STAGE/$PACKAGE_ROOT" <<'PYPROV'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

dst = Path(sys.argv[1])
manifest = {
    "version": os.environ["VERSION"],
    "source_revision": os.environ["SOURCE_REVISION"],
    "source_state": os.environ["SOURCE_STATE"],
    # 이 release의 mode 정책. 재현 검증 시 기대값을 밖에서 추측하지 않게 적어 둔다.
    "file_modes": {"regular": "0644", "executable": "0755"},
}
path = dst / "RELEASE_PROVENANCE.json"
path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
path.chmod(0o644)
PYPROV

"$PYTHON_BIN" - "$STAGE/$PACKAGE_ROOT" "$TMP_OUT" <<'PYZIP'
from __future__ import annotations
import os, sys, zipfile
from pathlib import Path

src = Path(sys.argv[1])   # staging/$PACKAGE_ROOT
out = sys.argv[2]          # dist/.<package>.zip.tmp.<pid> (임시 출력 경로)
pkg = src.name             # ai_model_serving_platform

_EPOCH = (1980, 1, 1, 0, 0, 0)

# 압축 수준을 명시한다. zipfile 기본값에 기대면 Python 기본이 바뀌는 날
# 같은 입력에서 다른 bytes가 나온다. (zlib 구현 차이는 여기서 없앨 수 없다.)
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    root_info = zipfile.ZipInfo(pkg + '/')
    root_info.date_time = _EPOCH
    root_info.external_attr = (0o755 << 16) | 0x10
    zf.writestr(root_info, '')

    for current, dirnames, filenames in os.walk(src):
        dirnames.sort()
        cur = Path(current)
        rel = cur.relative_to(src.parent)

        for d in sorted(dirnames):
            di = zipfile.ZipInfo(str(rel / d) + '/')
            di.date_time = _EPOCH
            di.external_attr = (0o755 << 16) | 0x10
            zf.writestr(di, '')

        for f in sorted(filenames):
            fp = cur / f
            fi = zipfile.ZipInfo(str(rel / f))
            fi.date_time = _EPOCH
            fi.compress_type = zipfile.ZIP_DEFLATED
            fi.external_attr = os.stat(fp).st_mode << 16
            with open(fp, 'rb') as fh:
                zf.writestr(fi, fh.read())
PYZIP

"$PYTHON_BIN" - "$TMP_OUT" "$PACKAGE_ROOT" <<'PYSELF'
from __future__ import annotations

import fnmatch
import sys
import zipfile

import yaml

out = sys.argv[1]
pkg = sys.argv[2]
forbidden_release_dirs = {".github", ".other", ".agents", ".codex", ".claude", ".cursor"}

with zipfile.ZipFile(out) as zf:
    names = zf.namelist()
    infos = zf.infolist()
    file_paths = {
        name[len(pkg) + 1 :]
        for name in names
        if name.startswith(f"{pkg}/") and not name.endswith("/")
    }
    contract_member = f"{pkg}/configs/env_contract.yaml"
    try:
        env_contract = yaml.safe_load(zf.read(contract_member))
    except KeyError as exc:
        raise SystemExit("Release ZIP is missing configs/env_contract.yaml") from exc
    if not isinstance(env_contract, dict):
        raise SystemExit("Release ZIP env contract must contain a mapping")
    env_examples = env_contract.get("env_examples")
    safe_env_examples = set(env_examples) if isinstance(env_examples, dict) else set()
    if not safe_env_examples:
        raise SystemExit("Release ZIP env contract does not declare env_examples")

# These are read when the Configuration Plane endpoint is imported and served.
# Keeping this release-artifact contract here prevents a future packaging
# exclusion from producing a ZIP that boots but fails only when an operator
# visits /admin/config/schema or /admin/config/effective on a fresh host.
required_configuration_plane_files = {
    "configs/configuration_schema.yaml",
    "src/ai_model_serving/configuration_plane.py",
    "src/ai_model_serving/api/routers/gateway_configuration.py",
}
missing_configuration_plane_files = required_configuration_plane_files - file_paths
if missing_configuration_plane_files:
    missing = ", ".join(sorted(missing_configuration_plane_files))
    raise SystemExit(f"Release ZIP is missing Configuration Plane runtime file(s): {missing}")

# /docs와 /redoc은 저장소에 vendoring 한 JS 번들을 같은 origin으로 서빙한다.
# CDN 폴백이 없으므로 번들이 빠진 ZIP은 기동은 하지만 문서 화면이 빈 화면이 된다.
# 위 Configuration Plane 검사와 같은 이유로 여기서 막는다. 기대 경로는
# ai_model_serving.docs_ui가 소유하므로 파일명을 여기 다시 적지 않는다.
sys.path.insert(0, "src")
from ai_model_serving.docs_ui import VENDORED_ASSETS  # noqa: E402

required_docs_assets = {
    f"src/ai_model_serving/static/{asset.filename}" for asset in VENDORED_ASSETS
}
missing_docs_assets = required_docs_assets - file_paths
if missing_docs_assets:
    missing = ", ".join(sorted(missing_docs_assets))
    raise SystemExit(
        f"Release ZIP is missing self-host documentation bundle(s): {missing}. "
        "git에 추가되지 않은 파일은 release에 담기지 않는다."
    )

# LICENSE와 NOTICE는 source release 자체의 일부다. 기존 ZIP 자체검사에서 확인하면
# 충분하므로 이 불변식만을 위한 별도 validator나 test suite는 두지 않는다.
required_legal_files = {"LICENSE", "NOTICE"}
missing_legal_files = required_legal_files - file_paths
if missing_legal_files:
    missing = ", ".join(sorted(missing_legal_files))
    raise SystemExit(f"Release ZIP is missing legal file(s): {missing}")

for path in sorted(file_paths):
    parts = path.split("/")
    if any(part in forbidden_release_dirs for part in parts):
        raise SystemExit(f"Release ZIP contains forbidden tool/private directory: {path}")
    name = path.rsplit("/", 1)[-1]
    if name not in safe_env_examples and (name == ".env" or fnmatch.fnmatch(name, ".env.*")):
        raise SystemExit(f"Release ZIP contains excluded environment file: {path}")

for name in names:
    rel = name[len(pkg) + 1 :] if name.startswith(f"{pkg}/") else name
    parts = [part for part in rel.split("/") if part]
    if any(part in forbidden_release_dirs for part in parts):
        raise SystemExit(f"Release ZIP contains forbidden tool/private directory: {name}")

epoch = (1980, 1, 1, 0, 0, 0)
if any(info.date_time != epoch for info in infos):
    raise SystemExit("Release ZIP contains non-reproducible timestamps")

# timestamp와 같은 이유로 mode도 고정값이어야 한다. Git이 기록하는 값은 실행 비트
# 하나뿐이므로 파일은 644/755, 디렉터리는 755다. 여기에 packager의 umask가 섞이면
# 같은 commit에서 서로 다른 ZIP이 나온다.
allowed_file_modes = {0o644, 0o755}
unexpected_modes = sorted(
    {
        (info.filename, oct(info.external_attr >> 16 & 0o7777))
        for info in infos
        if not info.filename.endswith("/")
        and (info.external_attr >> 16 & 0o7777) not in allowed_file_modes
    }
)
if unexpected_modes:
    shown = ", ".join(f"{name} ({mode})" for name, mode in unexpected_modes[:5])
    raise SystemExit(f"Release ZIP contains non-reproducible file modes: {shown}")
PYSELF

"$PYTHON_BIN" - "$TMP_OUT" "$OUT" <<'PYREPLACE'
from __future__ import annotations

import os
import sys

os.replace(sys.argv[1], sys.argv[2])
PYREPLACE

echo "$OUT"
