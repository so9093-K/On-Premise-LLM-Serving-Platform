from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse


CONSOLE_PREFIX = "/admin/console"
CONSOLE_ASSET_PREFIX = f"{CONSOLE_PREFIX}/assets/"
_CONSOLE_ROOT = Path(__file__).resolve().parent / "static" / "control-plane"

_CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    )
)
_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
_INDEX_HEADERS = {**_SECURITY_HEADERS, "Cache-Control": "no-cache"}
_ASSET_HEADERS = {
    **_SECURITY_HEADERS,
    "Cache-Control": "public, max-age=31536000, immutable",
}


def _manifest_asset_files(manifest_path: Path, assets_root: Path) -> dict[str, Path]:
    """Resolve the generated manifest into a trusted URL-to-file allowlist.

    Request path values must never participate in filesystem path construction. The
    checked-in Vite manifest is validated once during application composition and owns
    the exact set of immutable assets that the server may expose.
    """
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Admin Console asset manifest is unreadable or invalid") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("Admin Console asset manifest must be a JSON object")

    references: set[str] = set()
    for entry in manifest.values():
        if not isinstance(entry, dict):
            raise RuntimeError("Admin Console asset manifest entries must be objects")
        file_name = entry.get("file")
        if file_name is not None:
            if not isinstance(file_name, str):
                raise RuntimeError("Admin Console manifest file must be a string")
            references.add(file_name)
        for field in ("css", "assets"):
            values = entry.get(field, [])
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise RuntimeError(f"Admin Console manifest {field} must be a string array")
            references.update(values)

    files: dict[str, Path] = {}
    for reference in sorted(references):
        logical = PurePosixPath(reference)
        if (
            logical.is_absolute()
            or len(logical.parts) < 2
            or logical.parts[0] != "assets"
            or ".." in logical.parts
        ):
            raise RuntimeError(f"Admin Console manifest contains invalid asset path: {reference}")
        relative = PurePosixPath(*logical.parts[1:])
        candidate = (assets_root / Path(*relative.parts)).resolve()
        if not candidate.is_relative_to(assets_root) or not candidate.is_file():
            raise RuntimeError(f"Admin Console generated asset is missing or unsafe: {reference}")
        files[relative.as_posix()] = candidate

    if not files:
        raise RuntimeError("Admin Console asset manifest does not declare any generated assets")
    return files


def register_admin_console(app: FastAPI, *, root: Path | None = None) -> None:
    """Register the self-hosted Control Plane Console independently of API docs.

    The generated frontend is part of the installed Python package. Missing build
    artifacts are therefore a packaging error and fail application composition rather
    than degrading into a blank browser page at runtime.
    """
    console_root = (root or _CONSOLE_ROOT).resolve()
    index_path = console_root / "index.html"
    manifest_path = console_root / "asset-manifest.json"
    assets_root = (console_root / "assets").resolve()
    for required in (index_path, manifest_path):
        if not required.is_file():
            raise RuntimeError(f"Admin Console generated artifact is missing: {required}")
    asset_files = _manifest_asset_files(manifest_path, assets_root)

    @app.get(CONSOLE_PREFIX, include_in_schema=False)
    async def admin_console_redirect() -> RedirectResponse:
        return RedirectResponse(f"{CONSOLE_PREFIX}/", status_code=307)

    @app.get(f"{CONSOLE_PREFIX}/", include_in_schema=False)
    async def admin_console_index() -> FileResponse:
        return FileResponse(index_path, headers=_INDEX_HEADERS)

    @app.get(f"{CONSOLE_PREFIX}/assets/{{asset_path:path}}", include_in_schema=False)
    async def admin_console_asset(asset_path: str) -> FileResponse:
        candidate = asset_files.get(asset_path)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Admin Console asset not found.")
        return FileResponse(candidate, headers=_ASSET_HEADERS)

    @app.get(f"{CONSOLE_PREFIX}/{{client_path:path}}", include_in_schema=False)
    async def admin_console_client_route(client_path: str) -> FileResponse:
        # BrowserRouter owns client-side paths. The server only serves the same immutable
        # entry document; API and asset prefixes have more-specific routes above.
        return FileResponse(index_path, headers=_INDEX_HEADERS)
