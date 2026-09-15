from __future__ import annotations

from pathlib import Path

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

    @app.get(CONSOLE_PREFIX, include_in_schema=False)
    async def admin_console_redirect() -> RedirectResponse:
        return RedirectResponse(f"{CONSOLE_PREFIX}/", status_code=307)

    @app.get(f"{CONSOLE_PREFIX}/", include_in_schema=False)
    async def admin_console_index() -> FileResponse:
        return FileResponse(index_path, headers=_INDEX_HEADERS)

    @app.get(f"{CONSOLE_PREFIX}/assets/{{asset_path:path}}", include_in_schema=False)
    async def admin_console_asset(asset_path: str) -> FileResponse:
        candidate = (assets_root / asset_path).resolve()
        if not candidate.is_relative_to(assets_root) or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Admin Console asset not found.")
        return FileResponse(candidate, headers=_ASSET_HEADERS)

    @app.get(f"{CONSOLE_PREFIX}/{{client_path:path}}", include_in_schema=False)
    async def admin_console_client_route(client_path: str) -> FileResponse:
        # BrowserRouter owns client-side paths. The server only serves the same immutable
        # entry document; API and asset prefixes have more-specific routes above.
        return FileResponse(index_path, headers=_INDEX_HEADERS)
