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
  "$PYTHON_BIN" "$ROOT/scripts/build/check_python.py" --context package >/dev/null
  "$PYTHON_BIN" "$ROOT/scripts/validation/validate_contracts.py"
fi

"$PYTHON_BIN" - "$ROOT" "$STAGE/$PACKAGE_ROOT" <<'PYCODE'
from __future__ import annotations

import fnmatch
import json
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
    'models',
    'outputs',
    'run',
}
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
    if name in safe_env_examples:
        return False
    if name.endswith(exclude_suffixes):
        return True
    if any(fnmatch.fnmatch(name, pattern) for pattern in exclude_file_patterns):
        return True
    if len(rel_parts) >= 3 and rel_parts[0] == 'reports' and rel_parts[1] == 'runtime':
        if fnmatch.fnmatch(name, 'runtime_validation_*.json') or fnmatch.fnmatch(name, 'runtime_validation_*.md'):
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

# runtime validation report는 release 패키지에서 의도적으로 제외된다.
# 개발자가 패키징 전에 release/runtime validation을 실행했다면, 복사된
# live_evidence_bundle이 제외된 timestamped runtime report를 가리킬 수 있다.
# 패키징된 문서가 내부적으로 항상 일관되고 누락된 generated evidence를 참조하지
# 않도록, staging tree 안에서 이를 static placeholder로 다시 렌더링한다.
operator_bundle = dst / 'reports/runtime/operator_status_bundle.json'
if operator_bundle.exists():
    sys.path.insert(0, str(dst / 'src'))
    from ai_model_serving.live_evidence import live_evidence_bundle_document, write_live_evidence_bundle

    operator_status = json.loads(operator_bundle.read_text(encoding='utf-8'))
    version_file = dst / 'VERSION'
    version = version_file.read_text(encoding='utf-8').strip() if version_file.exists() else ''
    document = live_evidence_bundle_document(
        operator_status=operator_status,
        runtime_report=None,
        runtime_report_path=None,
        version=version,
        is_package_placeholder=True,
    )
    write_live_evidence_bundle(document, dst / 'reports/runtime')
    for cache_dir in dst.rglob('__pycache__'):
        shutil.rmtree(cache_dir, ignore_errors=True)

# package-time rewrite/exclusion이 모두 끝난 뒤 staged tree를 기준으로 packaged
# inventory를 다시 렌더링한다. 그래야 inventory가 ZIP 자체에 대한 source of truth가 된다.
sys.path.insert(0, str(dst / 'src'))
from ai_model_serving.project_inventory import write_inventory_reports

write_inventory_reports(dst)
PYCODE

# static validation을 위해 남겨둔 contract hygiene marker. 위의 staging copier가
# 안정적인 PACKAGE_ROOT를 압축하기 전에 이 exclusion/inclusion을 강제 적용한다.
# Exclude markers: "$BASE/.env.*" "$BASE/model_cache/*" "$BASE/models/*" "$BASE/logs/*" "$BASE/dist/*" "$BASE/run/*" "$BASE/**/*.pyc" "$BASE/**/*.egg-info/*"
# Runtime report exclude markers: reports/runtime/runtime_validation_*.json reports/runtime/runtime_validation_*.md; staged live_evidence_bundle은 timestamped runtime evidence 없이 재생성됨
# Safe env include markers: "$BASE/.env.example" "$BASE/.env.local.example" "$BASE/.env.compose.example"

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

import csv
import fnmatch
import sys
import zipfile

out = sys.argv[1]
pkg = sys.argv[2]
inventory_name = f"{pkg}/reports/refactor/project_inventory_current.csv"
safe_env_examples = {".env.example", ".env.local.example", ".env.compose.example"}
forbidden_release_dirs = {".other", ".agents", ".codex", ".claude", ".cursor"}

with zipfile.ZipFile(out) as zf:
    names = zf.namelist()
    file_paths = {
        name[len(pkg) + 1 :]
        for name in names
        if name.startswith(f"{pkg}/") and not name.endswith("/")
    }
    if inventory_name not in names:
        raise SystemExit(f"Missing packaged inventory: {inventory_name}")
    rows = list(csv.DictReader(zf.read(inventory_name).decode("utf-8").splitlines()))

inventory_paths = {row["path"] for row in rows}
for path in sorted(inventory_paths):
    parts = path.split("/")
    if any(part in forbidden_release_dirs for part in parts):
        raise SystemExit(f"Packaged inventory contains forbidden tool/private directory: {path}")
    name = path.rsplit("/", 1)[-1]
    if name not in safe_env_examples and (name == ".env" or fnmatch.fnmatch(name, ".env.*")):
        raise SystemExit(f"Packaged inventory contains excluded environment file: {path}")

missing_from_zip = sorted(inventory_paths - file_paths)
missing_from_inventory = sorted(file_paths - inventory_paths)
if missing_from_zip or missing_from_inventory:
    lines = ["Packaged inventory does not match ZIP file list."]
    if missing_from_zip:
        lines.append("Inventory-only paths: " + ", ".join(missing_from_zip[:20]))
    if missing_from_inventory:
        lines.append("ZIP-only paths: " + ", ".join(missing_from_inventory[:20]))
    raise SystemExit("\n".join(lines))

for name in names:
    rel = name[len(pkg) + 1 :] if name.startswith(f"{pkg}/") else name
    parts = [part for part in rel.split("/") if part]
    if any(part in forbidden_release_dirs for part in parts):
        raise SystemExit(f"Release ZIP contains forbidden tool/private directory: {name}")
PYSELF

"$PYTHON_BIN" - "$TMP_OUT" "$OUT" <<'PYREPLACE'
from __future__ import annotations

import os
import sys

os.replace(sys.argv[1], sys.argv[2])
PYREPLACE

echo "$OUT"
