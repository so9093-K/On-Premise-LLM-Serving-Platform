from __future__ import annotations

import json
import logging
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from .errors import request_id_from_headers
# service_logger/scrub_for_log은 starlette 없이도 써야 하는 호출부(예: 순수 YAML/카탈로그
# 검증 스크립트가 도는 최소 venv)가 있어 service_logging.py로 분리했다 — 여기서는
# 하위호환을 위해 재수출만 한다.
from .service_logging import scrub_for_log, service_logger

__all__ = [
    "scrub_for_log",
    "service_logger",
    "safe_request_log_record",
    "log_request_completion",
    "safe_request_logging_middleware",
]


def safe_request_log_record(
    *,
    service: str,
    request: Request,
    status_code: int,
    elapsed_seconds: float,
    error_code: str | None = None,
    error_message: str | None = None,
    response_request_id: str | None = None,
) -> dict[str, Any]:
    route_obj = request.scope.get("route")
    route = getattr(route_obj, "path", None) or request.url.path
    peer_host = request.client.host if request.client else None
    record = {
        "event": "http_request_completed",
        "service": service,
        # 에러 응답은 요청에 x-request-id가 없어도 error_payload가 request_id를 새로
        # 발급하고 그 값을 X-Request-Id 응답 헤더로 에코한다(errors.py의
        # error_response_headers). 그 값을 우선해야 클라이언트가 실제로 받은
        # request_id와 로그의 request_id가 항상 일치한다 — 아니면 x-request-id를
        # 안 보낸 클라이언트의 에러는 로그에서 request_id로 절대 못 찾는다.
        "request_id": response_request_id or request_id_from_headers(request.headers),
        "method": request.method,
        "route": route,
        "status_code": status_code,
        "latency_ms": round(elapsed_seconds * 1000, 3),
        "client_host": peer_host,
    }
    if error_code:
        record["error_code"] = error_code
    if error_message:
        record["error_message"] = error_message
    return scrub_for_log(record)


def log_request_completion(
    *,
    logger: logging.Logger,
    service: str,
    request: Request,
    status_code: int,
    elapsed_seconds: float,
    error_code: str | None = None,
    error_message: str | None = None,
    response_request_id: str | None = None,
) -> None:
    record = safe_request_log_record(
        service=service,
        request=request,
        status_code=status_code,
        elapsed_seconds=elapsed_seconds,
        error_code=error_code,
        error_message=error_message,
        response_request_id=response_request_id,
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
    error_code: str | None = None
    error_message: str | None = None
    response_request_id: str | None = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        error_code = response.headers.get("x-error-code")
        error_message = response.headers.get("x-error-message")
        response_request_id = response.headers.get("x-request-id")
        return response
    finally:
        log_request_completion(
            logger=logger,
            service=service,
            request=request,
            status_code=status_code,
            elapsed_seconds=time.monotonic() - start,
            error_code=error_code,
            error_message=error_message,
            response_request_id=response_request_id,
        )
