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

"$PYTHON_BIN" - "$ROOT" "$STAGE/$PACKAGE_ROOT" <<'PYCODE'
from __future__ import annotations

import fnmatch
import os
import shutil
import sys
from pathlib import Path

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
    '.other',
    'build',
    'dist',
    'env',
    'logs',
    'model_cache',
    # 모델 카드는 docs/reference/models/로 이관됐다. 로컬에 남은 빈 레거시
    # 디렉터리가 배포 ZIP에 섞이지 않게 한다.
    'model_cards',
    'models',
    'outputs',
    'run',
}
# tests는 포함한다. bootstrap(make first-run)이 `make test`를 배포 전 게이트로
# 부르므로, 테스트가 빠진 ZIP은 문서화된 진입점이 no tests collected(exit 5)로
# 중단된다 -- 게이트를 부르면서 게이트 입력을 빼는 구성이었다.
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
safe_env_examples = {'.env.example', '.env.local.example', '.env.compose.example'}


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

for current, dirnames, filenames in os.walk(src):
    current_path = Path(current)
    rel_current = current_path.relative_to(src)
    rel_parts = () if str(rel_current) == '.' else rel_current.parts

    kept_dirs = []
    for dirname in sorted(dirnames):
        child_parts = rel_parts + (dirname,)
        if not skip_dir(child_parts, dirname):
            kept_dirs.append(dirname)
    dirnames[:] = kept_dirs

    target_dir = dst / rel_current if str(rel_current) != '.' else dst
    target_dir.mkdir(parents=True, exist_ok=True)

    for filename in sorted(filenames):
        rel_file_parts = rel_parts + (filename,)
        if skip_file(rel_file_parts, filename):
            continue
        source_file = current_path / filename
        target_file = dst.joinpath(*rel_file_parts)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)

PYCODE

"$PYTHON_BIN" - "$STAGE/$PACKAGE_ROOT" "$TMP_OUT" <<'PYZIP'
from __future__ import annotations
import os, sys, zipfile
from pathlib import Path

src = Path(sys.argv[1])   # staging/$PACKAGE_ROOT
out = sys.argv[2]          # dist/.<package>.zip.tmp.<pid> (임시 출력 경로)
pkg = src.name             # ai_model_serving_platform

_EPOCH = (1980, 1, 1, 0, 0, 0)

with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
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

out = sys.argv[1]
pkg = sys.argv[2]
safe_env_examples = {".env.example", ".env.local.example", ".env.compose.example"}
forbidden_release_dirs = {".other", ".agents", ".codex", ".claude", ".cursor"}

with zipfile.ZipFile(out) as zf:
    names = zf.namelist()
    infos = zf.infolist()
    file_paths = {
        name[len(pkg) + 1 :]
        for name in names
        if name.startswith(f"{pkg}/") and not name.endswith("/")
    }

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
PYSELF

"$PYTHON_BIN" - "$TMP_OUT" "$OUT" <<'PYREPLACE'
from __future__ import annotations

import os
import sys

os.replace(sys.argv[1], sys.argv[2])
PYREPLACE

echo "$OUT"
