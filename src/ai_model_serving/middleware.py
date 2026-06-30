from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from .errors import error_response, request_id_from_headers


WRITE_METHODS = {"POST", "PUT", "PATCH"}


def _too_large_response(request: Request, max_body_bytes: int) -> Response:
    return error_response(
        "REQUEST_TOO_LARGE",
        f"Request body exceeds {max_body_bytes} bytes. Limit applies to the full JSON body, including base64 media; reduce media size or split the request.",
        False,
        413,
        request_id_from_headers(request.headers),
    )


def _content_length_exceeds_limit(request: Request, max_body_bytes: int) -> bool:
    raw_value = request.headers.get("content-length")
    if raw_value is None:
        return False
    try:
        return int(raw_value) > max_body_bytes
    except ValueError:
        # Let the ASGI server/framework handle malformed Content-Length values.
        return False


async def _read_limited_body(request: Request, *, max_body_bytes: int) -> bytes | None:
    """Read the request body up to ``max_body_bytes``.

    Returns ``None`` as soon as the limit is exceeded. This avoids buffering an
    arbitrarily large request body in memory before rejecting it, while still
    replaying an already-validated body to downstream FastAPI handlers.
    """
    chunks: list[bytes] = []
    total = 0
    more_body = True

    while more_body:
        message: dict[str, Any] = await request.receive()
        if message.get("type") == "http.disconnect":
            return b""

        chunk = message.get("body", b"")
        if chunk:
            total += len(chunk)
            if total > max_body_bytes:
                return None
            chunks.append(chunk)

        more_body = bool(message.get("more_body", False))

    return b"".join(chunks)



async def enforce_request_body_limit(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    *,
    max_body_bytes: int,
) -> Response:
    """Reject oversized write requests based on actual body size.

    Content-Length is used as a fast rejection path when available. Chunked or
    missing-length bodies are read incrementally and rejected as soon as the
    configured limit is crossed, instead of calling ``request.body()`` and
    buffering the entire oversized payload first.
    """
    if request.method not in WRITE_METHODS:
        return await call_next(request)

    if _content_length_exceeds_limit(request, max_body_bytes):
        return _too_large_response(request, max_body_bytes)

    body = await _read_limited_body(request, max_body_bytes=max_body_bytes)
    if body is None:
        return _too_large_response(request, max_body_bytes)

    # Starlette's function-style middleware wraps requests in a cached request
    # object. Populate the cache after incremental reading so downstream request
    # parsing can consume the same validated body without touching the original
    # receive channel again.
    setattr(request, "_body", body)
    return await call_next(request)
