from __future__ import annotations

import json
import logging
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from .detectors.masking import mask_sensitive_text
from .errors import request_id_from_headers
from .contracts.common import is_int
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
    "record_error_diagnosis",
    "record_readiness_failure",
]


_DIAGNOSIS_TEXT_LIMIT = 500
# Docker json-file 드라이버는 로그 메시지를 16KiB에서 잘라 여러 줄로 쪼갠다. 쪼개진
# 조각은 각각 유효한 JSON이 아니라서 Loki의 `| json`과 Grafana의 extractFields가
# 레코드 전체를 잃는다. 실제로 12,616 token 응답 하나가 약 19KB 라인이 되어 3조각으로
# 갈렸고, JSON 키가 알파벳 순이라 절단점 뒤의 prompt_tokens/route/status_code/
# total_tokens가 통째로 사라졌다. 두 body를 합쳐도 한 줄이 그 한도 안에 들어오도록
# 자른다. 한도는 문자 수가 아니라 UTF-8 바이트다 -- 한글은 문자당 3바이트라 문자 수로
# 자르면 같은 한도에서 3배 커진다.
_BODY_PREVIEW_LIMIT_BYTES = 4000
_TRUNCATION_SUFFIX = "…(truncated)"


def _truncate_preview(text: str) -> str:
    """로그 한 줄이 Docker의 메시지 한도를 넘지 않도록 body preview를 자른다."""
    encoded = text.encode("utf-8")
    if len(encoded) <= _BODY_PREVIEW_LIMIT_BYTES:
        return text
    # multi-byte 문자 중간에서 잘린 꼬리는 버린다.
    return encoded[:_BODY_PREVIEW_LIMIT_BYTES].decode("utf-8", errors="ignore") + _TRUNCATION_SUFFIX


def _safe_diagnosis_text(value: Any) -> str | None:
    """운영 로그에 남길 짧고 마스킹된 진단 문자열을 만든다."""
    if not isinstance(value, str):
        return None
    text = mask_sensitive_text(value.strip())
    if not text:
        return None
    return text[:_DIAGNOSIS_TEXT_LIMIT]


def record_error_diagnosis(
    request: Request,
    *,
    code: str,
    retryable: bool,
    debug: dict[str, Any] | None = None,
) -> None:
    """표준 API 오류의 안전한 진단 요약만 access log에 전달한다.

    ``error.debug`` 전체에는 upstream body처럼 로그에 영구 보관하면 안 되는 값이
    포함될 수 있다. 원인 type/message와 upstream HTTP 상태처럼 운영자가 바로
    분류에 쓰는 allowlist만 별도 필드로 남긴다.
    """
    request.state.error_code = code
    request.state.error_retryable = retryable
    if not debug:
        return
    cause_type = _safe_diagnosis_text(debug.get("cause_type"))
    cause_message = _safe_diagnosis_text(debug.get("cause_message"))
    upstream_status = debug.get("upstream_status")
    if cause_type:
        request.state.error_cause_type = cause_type
    if cause_message:
        request.state.error_cause_message = cause_message
    if is_int(upstream_status) and 100 <= upstream_status <= 599:
        request.state.error_upstream_status = upstream_status


def record_readiness_failure(request: Request, body: dict[str, Any]) -> None:
    """503 readiness 응답의 dependency 원인을 access log에 요약한다.

    readiness는 API error envelope가 아니므로 error_code로 억지 분류하지 않는다.
    이미 관리자 응답 본문에 있는 dependency 이름과 마스킹된 상태 설명만 남겨
    별도 운영 패널에서 즉시 원인을 확인할 수 있게 한다.
    """
    if body.get("status") == "ready":
        return
    dependencies = body.get("dependencies")
    if not isinstance(dependencies, list):
        return
    names: list[str] = []
    summaries: list[str] = []
    for dependency in dependencies:
        if not isinstance(dependency, dict) or dependency.get("status") == "ready":
            continue
        name = _safe_diagnosis_text(dependency.get("name"))
        if not name:
            continue
        names.append(name)
        detail = _safe_diagnosis_text(dependency.get("message")) or _safe_diagnosis_text(dependency.get("status"))
        summaries.append(f"{name}: {detail}" if detail else name)
    request.state.readiness_status = str(body.get("status") or "not_ready")
    request.state.readiness_dependencies = ", ".join(names)
    request.state.readiness_summary = "; ".join(summaries)


def record_request_response_preview(request: Request, *, request_text: str, response_text: str) -> None:
    """request.state에 마스킹된 요청/응답 프리뷰를 남긴다.

    `LOG_REQUEST_RESPONSE_BODY=true`일 때만 호출해야 한다 -- 플래그 확인은
    호출자 책임이다(safe_request_log_record가 이 값이 있을 때만 로그 레코드에
    싣는다). 어떤 엔드포인트든 같은 마스킹 규칙(mask_sensitive_text)을 거치므로,
    각 라우터가 `request.state.request_body_masked = ...`를 직접 대입하며
    코드를 복제하지 않게 한다.
    """
    request.state.request_body_masked = _truncate_preview(mask_sensitive_text(request_text))
    request.state.response_body_masked = _truncate_preview(mask_sensitive_text(response_text))


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
        if is_int(value) and value >= 0:
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
    resolved_error_code = error_code or getattr(request.state, "error_code", None)
    if resolved_error_code:
        record["error_code"] = resolved_error_code
    if error_message:
        record["error_message"] = error_message
    for field in ("error_retryable", "error_cause_type", "error_cause_message", "error_upstream_status"):
        value = getattr(request.state, field, None)
        if value is not None:
            record[field] = value
    for field in ("readiness_status", "readiness_dependencies", "readiness_summary"):
        value = getattr(request.state, field, None)
        if value:
            record[field] = value
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
