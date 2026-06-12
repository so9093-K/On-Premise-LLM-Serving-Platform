from __future__ import annotations

import json
import logging
import hashlib
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from .errors import request_id_from_headers

SENSITIVE_KEYS = {
    "prompt",
    "raw_prompt",
    "messages",
    "input",
    "authorization",
    "api_key",
    "token",
    "password",
    "secret",
    "generated_text",
    "model_output",
}


def scrub_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else scrub_for_log(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_for_log(item) for item in value]
    return value


def service_logger(service: str) -> logging.Logger:
    logger = logging.getLogger(f"ai_model_serving.{service}")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


def _first_forwarded_for(headers: Any) -> str | None:
    value = headers.get("x-forwarded-for") if hasattr(headers, "get") else None
    if not isinstance(value, str):
        return None
    first = value.split(",", 1)[0].strip()
    return first or None


def _client_ip_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def safe_request_log_record(
    *,
    service: str,
    request: Request,
    status_code: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    route_obj = request.scope.get("route")
    route = getattr(route_obj, "path", None) or request.url.path
    peer_host = request.client.host if request.client else None
    forwarded_for = _first_forwarded_for(request.headers)
    client_ip_for_correlation = forwarded_for or peer_host
    record = {
        "event": "http_request_completed",
        "service": service,
        "request_id": request_id_from_headers(request.headers),
        "method": request.method,
        "route": route,
        "status_code": status_code,
        "latency_ms": round(elapsed_seconds * 1000, 3),
        "client_host": peer_host,
        "client_ip_hash": _client_ip_hash(client_ip_for_correlation),
        "forwarded_for_present": forwarded_for is not None,
        "forwarded_proto": request.headers.get("x-forwarded-proto"),
    }
    return scrub_for_log(record)


def log_request_completion(
    *,
    logger: logging.Logger,
    service: str,
    request: Request,
    status_code: int,
    elapsed_seconds: float,
) -> None:
    record = safe_request_log_record(
        service=service,
        request=request,
        status_code=status_code,
        elapsed_seconds=elapsed_seconds,
    )
    logger.info(json.dumps(record, ensure_ascii=False, sort_keys=True))


async def safe_request_logging_middleware(
    request: Request,
    call_next,
    *,
    logger: logging.Logger,
    service: str,
) -> Response:
    start = time.monotonic()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        log_request_completion(
            logger=logger,
            service=service,
            request=request,
            status_code=status_code,
            elapsed_seconds=time.monotonic() - start,
        )
