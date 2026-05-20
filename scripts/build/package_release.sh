#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.12 || command -v python3 || command -v python)}"
VERSION="$(cat "$ROOT/VERSION")"
PACKAGE_NAME="${PACKAGE_NAME:-ai_model_serving_platform}"
PACKAGE_ROOT="${PACKAGE_ROOT:-ai_model_serving_platform}"
DIST="$ROOT/dist"
OUT="$DIST/${PACKAGE_NAME}_${VERSION}.zip"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/ai-model-serving-package.XXXXXX")"

cleanup() {
  rm -rf "$STAGE"
}
trap cleanup EXIT

mkdir -p "$DIST"
rm -f "$OUT"

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
    'env',
    'venv',
    '__pycache__',
    'model_cache',
}
exclude_top_level_dirs = {
    '.other',
    'build',
    'dist',
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

# Runtime validation reports are intentionally excluded from release packages.
# If a developer ran release/runtime validation before packaging, the copied
# live_evidence_bundle may point at an excluded timestamped runtime report.
# Re-render it inside the staging tree as a static placeholder so packaged docs
# remain internally consistent and never reference missing generated evidence.
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
    )
    write_live_evidence_bundle(document, dst / 'reports/runtime')
    for cache_dir in dst.rglob('__pycache__'):
        shutil.rmtree(cache_dir, ignore_errors=True)

# Re-render the packaged inventory from the staged tree after all package-time
# rewrites/exclusions, so the inventory is a source of truth for the ZIP itself.
sys.path.insert(0, str(dst / 'src'))
from ai_model_serving.project_inventory import write_inventory_reports

write_inventory_reports(dst)
PYCODE

# Contract hygiene markers retained for static validation. The staging copier above enforces these
# exclusions/inclusions before zipping the stable PACKAGE_ROOT.
# Exclude markers: "$BASE/.env.*" "$BASE/model_cache/*" "$BASE/models/*" "$BASE/logs/*" "$BASE/dist/*" "$BASE/run/*" "$BASE/**/*.pyc" "$BASE/**/*.egg-info/*"
# Runtime report exclude markers: reports/runtime/runtime_validation_*.json reports/runtime/runtime_validation_*.md; staged live_evidence_bundle is regenerated without timestamped runtime evidence
# Safe env include markers: "$BASE/.env.example" "$BASE/.env.local.example" "$BASE/.env.compose.example"

"$PYTHON_BIN" - "$STAGE/$PACKAGE_ROOT" "$OUT" <<'PYZIP'
from __future__ import annotations
import os, sys, zipfile
from pathlib import Path

src = Path(sys.argv[1])   # staging/$PACKAGE_ROOT
out = sys.argv[2]          # dist/...zip
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

"$PYTHON_BIN" - "$OUT" "$PACKAGE_ROOT" <<'PYSELF'
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

echo "$OUT"
