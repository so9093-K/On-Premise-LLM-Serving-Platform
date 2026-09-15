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

# Release file selection, canonical mode, source identity and per-file hash are owned by
# one resolver. Remote deployment calls the same materializer instead of maintaining a
# second git-ls-files/exclude implementation.
"$PYTHON_BIN" scripts/release/release_artifact.py materialize \
  --source "$ROOT" \
  --destination "$STAGE/$PACKAGE_ROOT" \
  --version "$VERSION" >/dev/null

# Validate the staging tree itself, not a hand-maintained list of Python modules. A new
# internal module is therefore automatically covered when it becomes part of the release.
# This smoke must not mutate the immutable candidate with host-specific __pycache__ files.
PYTHONDONTWRITEBYTECODE=1 \
APP_CONFIG_ROOT="$STAGE/$PACKAGE_ROOT" \
  "$PYTHON_BIN" - "$STAGE/$PACKAGE_ROOT" <<'PYSMOKE'
from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
os.chdir(root)
sys.path.insert(0, str(root / "src"))

from ai_model_serving.apps.gateway import create_gateway_app  # noqa: F401,E402
from ai_model_serving.configuration_plane import configuration_schema  # noqa: E402

schema = configuration_schema()
if not isinstance(schema, dict) or not schema.get("items"):
    raise SystemExit("Release staging Configuration Plane schema is empty or invalid")
PYSMOKE

"$PYTHON_BIN" - "$STAGE/$PACKAGE_ROOT" "$TMP_OUT" <<'PYZIP'
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

src = Path(sys.argv[1])
out = sys.argv[2]
pkg = src.name
_EPOCH = (1980, 1, 1, 0, 0, 0)

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    root_info = zipfile.ZipInfo(pkg + "/")
    root_info.date_time = _EPOCH
    root_info.external_attr = (0o755 << 16) | 0x10
    zf.writestr(root_info, "")

    for current, dirnames, filenames in os.walk(src):
        dirnames.sort()
        cur = Path(current)
        rel = cur.relative_to(src.parent)
        for dirname in dirnames:
            info = zipfile.ZipInfo(str(rel / dirname) + "/")
            info.date_time = _EPOCH
            info.external_attr = (0o755 << 16) | 0x10
            zf.writestr(info, "")
        for filename in sorted(filenames):
            path = cur / filename
            info = zipfile.ZipInfo(str(rel / filename))
            info.date_time = _EPOCH
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = os.stat(path).st_mode << 16
            with path.open("rb") as handle:
                zf.writestr(info, handle.read())
PYZIP

# ZIP transport must preserve the canonical materialized tree exactly. Re-check hashes,
# sizes, modes and file-set from RELEASE_MANIFEST instead of duplicating release excludes.
"$PYTHON_BIN" - "$TMP_OUT" "$PACKAGE_ROOT" <<'PYSELF'
from __future__ import annotations

import hashlib
import json
import sys
import zipfile

out = sys.argv[1]
pkg = sys.argv[2]
prefix = f"{pkg}/"
manifest_member = prefix + "RELEASE_MANIFEST.json"
provenance_member = prefix + "RELEASE_PROVENANCE.json"

with zipfile.ZipFile(out) as zf:
    infos = zf.infolist()
    by_name = {info.filename: info for info in infos}
    try:
        manifest = json.loads(zf.read(manifest_member))
        provenance = json.loads(zf.read(provenance_member))
    except KeyError as exc:
        raise SystemExit("Release ZIP is missing canonical release metadata") from exc

    files = manifest.get("files")
    if not isinstance(files, list):
        raise SystemExit("Release ZIP manifest files must be a list")
    expected_payload = {entry["path"] for entry in files}
    actual_payload = {
        name[len(prefix):]
        for name in by_name
        if name.startswith(prefix)
        and not name.endswith("/")
        and name not in {manifest_member, provenance_member}
    }
    if actual_payload != expected_payload:
        raise SystemExit(
            "Release ZIP payload differs from RELEASE_MANIFEST: "
            f"missing={sorted(expected_payload - actual_payload)[:5]}, "
            f"unexpected={sorted(actual_payload - expected_payload)[:5]}"
        )

    for entry in files:
        member = prefix + entry["path"]
        info = by_name[member]
        data = zf.read(member)
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise SystemExit(f"Release ZIP payload hash mismatch: {entry['path']}")
        mode = info.external_attr >> 16 & 0o7777
        if mode != int(entry["mode"], 8):
            raise SystemExit(f"Release ZIP payload mode mismatch: {entry['path']}")

    expected_provenance = {
        "version": manifest["version"],
        "source_revision": manifest["source"]["revision"],
        "source_state": manifest["source"]["tracked_state"],
        "payload_sha256": manifest["payload_sha256"],
        "file_modes": manifest["file_modes"],
        "derived_from": "RELEASE_MANIFEST.json",
    }
    if provenance != expected_provenance:
        raise SystemExit("RELEASE_PROVENANCE.json is not derived from RELEASE_MANIFEST.json")

    # /docs and /redoc have no CDN fallback. Keep this functional release invariant
    # separate from selection policy: the manifest is canonical, but an incomplete
    # canonical payload must still fail packaging.
    sys.path.insert(0, "src")
    from ai_model_serving.docs_ui import VENDORED_ASSETS  # noqa: E402

    required_docs_assets = {
        f"src/ai_model_serving/static/{asset.filename}" for asset in VENDORED_ASSETS
    }
    missing_docs_assets = required_docs_assets - actual_payload
    if missing_docs_assets:
        raise SystemExit(
            "Release ZIP is missing self-host documentation bundle(s): "
            + ", ".join(sorted(missing_docs_assets))
        )
    console_root = "src/ai_model_serving/static/control-plane/"
    console_index = console_root + "index.html"
    console_manifest_member = console_root + "asset-manifest.json"
    missing_console_entry = {console_index, console_manifest_member} - actual_payload
    if missing_console_entry:
        raise SystemExit(
            "Release ZIP is missing Control Plane Console entry artifact(s): "
            + ", ".join(sorted(missing_console_entry))
        )
    try:
        console_manifest = json.loads(zf.read(prefix + console_manifest_member))
    except (KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("Release ZIP has invalid Control Plane Console asset manifest") from exc
    if not isinstance(console_manifest, dict):
        raise SystemExit("Control Plane Console asset manifest must be an object")
    required_console_assets = set()
    for entry in console_manifest.values():
        if not isinstance(entry, dict):
            raise SystemExit("Control Plane Console manifest entries must be objects")
        file_name = entry.get("file")
        if isinstance(file_name, str):
            required_console_assets.add(console_root + file_name)
        for field in ("css", "assets"):
            values = entry.get(field, [])
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise SystemExit(f"Control Plane Console manifest {field} must be a string array")
            required_console_assets.update(console_root + item for item in values)
    missing_console_assets = required_console_assets - actual_payload
    if missing_console_assets:
        raise SystemExit(
            "Release ZIP is missing Control Plane Console generated asset(s): "
            + ", ".join(sorted(missing_console_assets))
        )

    missing_legal = {"LICENSE", "NOTICE"} - actual_payload
    if missing_legal:
        raise SystemExit("Release ZIP is missing legal file(s): " + ", ".join(sorted(missing_legal)))

    epoch = (1980, 1, 1, 0, 0, 0)
    if any(info.date_time != epoch for info in infos):
        raise SystemExit("Release ZIP contains non-reproducible timestamps")
    for info in infos:
        mode = info.external_attr >> 16 & 0o7777
        expected_mode = 0o755 if info.filename.endswith("/") else (
            0o644 if info.filename in {manifest_member, provenance_member} else mode
        )
        if info.filename.endswith("/") and mode != expected_mode:
            raise SystemExit(f"Release ZIP directory mode mismatch: {info.filename}")
        if info.filename in {manifest_member, provenance_member} and mode != expected_mode:
            raise SystemExit(f"Release ZIP metadata mode mismatch: {info.filename}")
PYSELF

"$PYTHON_BIN" - "$TMP_OUT" "$OUT" <<'PYREPLACE'
from __future__ import annotations

import os
import sys

os.replace(sys.argv[1], sys.argv[2])
PYREPLACE

echo "$OUT"
