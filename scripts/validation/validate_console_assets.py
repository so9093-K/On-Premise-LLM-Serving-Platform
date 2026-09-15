from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "ui" / "control-plane"
DIST_ROOT = ROOT / "src" / "ai_model_serving" / "static" / "control-plane"


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid or missing Console JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Console JSON artifact must be an object: {path}")
    return value


def _manifest_files(manifest: dict) -> set[str]:
    files: set[str] = set()
    for key, raw in manifest.items():
        if not isinstance(key, str) or not isinstance(raw, dict):
            raise SystemExit("Console Vite manifest entries must be objects keyed by strings")
        file_name = raw.get("file")
        if isinstance(file_name, str):
            files.add(file_name)
        for field in ("css", "assets"):
            values = raw.get(field, [])
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise SystemExit(f"Console Vite manifest {field} must be a string array: {key}")
            files.update(values)
    return files


def _validate_source_policy() -> None:
    forbidden = ("localStorage", "sessionStorage")
    for path in sorted((SOURCE_ROOT / "src").rglob("*")):
        if not path.is_file() or path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                raise SystemExit(
                    f"Admin Console credential policy forbids {token} in browser source: {path}"
                )


def main() -> int:
    package = _load_json(SOURCE_ROOT / "package.json")
    lock = _load_json(SOURCE_ROOT / "package-lock.json")
    if lock.get("lockfileVersion") != 3:
        raise SystemExit("Admin Console package-lock.json must use npm lockfileVersion 3")
    if lock.get("name") != package.get("name") or lock.get("version") != package.get("version"):
        raise SystemExit("Admin Console package.json and package-lock.json identity differ")

    index_path = DIST_ROOT / "index.html"
    manifest_path = DIST_ROOT / "asset-manifest.json"
    if not index_path.is_file() or not manifest_path.is_file():
        raise SystemExit("Admin Console checked-in build is incomplete")

    index = index_path.read_text(encoding="utf-8")
    if re.search(r"(?:src|href)=[\"']https?://", index, flags=re.IGNORECASE):
        raise SystemExit("Admin Console index must not load external runtime assets")
    if "/admin/console/assets/" not in index:
        raise SystemExit("Admin Console index must reference same-origin hashed assets")

    manifest = _load_json(manifest_path)
    referenced = _manifest_files(manifest)
    if not referenced:
        raise SystemExit("Admin Console Vite manifest contains no generated assets")
    for relative in sorted(referenced):
        path = (DIST_ROOT / relative).resolve()
        if not path.is_relative_to(DIST_ROOT.resolve()) or not path.is_file():
            raise SystemExit(f"Admin Console manifest references missing/unsafe asset: {relative}")

    for css in sorted(DIST_ROOT.glob("assets/*.css")):
        text = css.read_text(encoding="utf-8", errors="replace")
        if re.search(r"url\(\s*[\"']?https?://", text, flags=re.IGNORECASE):
            raise SystemExit(f"Admin Console CSS contains an external runtime asset URL: {css}")

    _validate_source_policy()
    print(f"Admin Console artifacts valid ({len(referenced)} manifest assets).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
