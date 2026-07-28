from __future__ import annotations

import json
import logging
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from .detectors.masking import mask_sensitive_text
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
    "record_request_response_preview",
    "record_token_usage",
]


def record_request_response_preview(request: Request, *, request_text: str, response_text: str) -> None:
    """request.state에 마스킹된 요청/응답 프리뷰를 남긴다.

    `LOG_REQUEST_RESPONSE_BODY=true`일 때만 호출해야 한다 -- 플래그 확인은
    호출자 책임이다(safe_request_log_record가 이 값이 있을 때만 로그 레코드에
    싣는다). 어떤 엔드포인트든 같은 마스킹 규칙(mask_sensitive_text)을 거치므로,
    각 라우터가 `request.state.request_body_masked = ...`를 직접 대입하며
    코드를 복제하지 않게 한다.
    """
    request.state.request_body_masked = mask_sensitive_text(request_text)
    request.state.response_body_masked = mask_sensitive_text(response_text)


def record_token_usage(request: Request, usage: Any) -> None:
    """request.state에 토큰 사용량(prompt/completion/total)을 남긴다.

    프롬프트/응답 원문과 달리 토큰 개수는 민감정보가 아니므로
    `LOG_REQUEST_RESPONSE_BODY`와 무관하게 latency_ms와 동급으로 항상 호출한다
    -- 호출자가 플래그를 확인할 필요 없음. usage가 없거나(예: 업스트림이
    응답에 안 실은 경우) 모양이 안 맞으면 조용히 아무것도 안 남긴다.
    """
    if not isinstance(usage, dict):
        return
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(field)
        if isinstance(value, int):
            setattr(request.state, field, value)


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
    # 토큰 개수는 민감정보가 아니라 LOG_REQUEST_RESPONSE_BODY와 무관하게 항상
    # 채워질 수 있다(record_token_usage가 usage를 실은 엔드포인트에 한해).
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(request.state, field, None)
        if value is not None:
            record[field] = value
    # LOG_REQUEST_RESPONSE_BODY=true일 때만 채워진다(gateway_inference.py의
    # chat_completions, non-streaming 한정). 이미 masking.mask_sensitive_text로
    # PII/secret을 치환한 텍스트라 scrub_for_log가 추가로 지우지 않는다.
    request_body = getattr(request.state, "request_body_masked", None)
    if request_body is not None:
        record["request_body"] = request_body
    response_body = getattr(request.state, "response_body_masked", None)
    if response_body is not None:
        record["response_body"] = response_body
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
