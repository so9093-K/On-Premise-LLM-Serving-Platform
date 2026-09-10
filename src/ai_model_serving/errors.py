from __future__ import annotations

import math
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import KW_ONLY, dataclass
from typing import Any
from uuid import uuid4

from fastapi.responses import JSONResponse


# 같은 ASGI 요청의 handler·직접 JSONResponse·SSE generator가 공유한다.
# response header를 내부 통신 수단으로 쓰지 않고 scope의 state에 기록한다.
REQUEST_ERROR_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("request_error_context", default=None)


@dataclass(frozen=True)
class ErrorDefinition:
    """한 공개 오류 코드의 transport 기본값.

    ``status_code=None``은 HTTP 응답이 아니라 이미 시작된 SSE stream 안에서만
    전달되는 종료 이벤트를 뜻한다.
    """

    status_code: int | None
    retryable: bool


# 공개 error code의 런타임 Source of Truth. status와 retryable을 별도 표로
# 복제하지 않는다. meaning/action은 configs/error_catalog.yaml이 문서 계층으로
# 소유하고 validate_common_error_codes가 코드 집합의 일치를 확인한다.
ERROR_DEFINITIONS: dict[str, ErrorDefinition] = {
    "VALIDATION_ERROR": ErrorDefinition(422, False),
    "UNAUTHORIZED": ErrorDefinition(401, False),
    "FORBIDDEN": ErrorDefinition(403, False),
    "NOT_FOUND": ErrorDefinition(404, False),
    "METHOD_NOT_ALLOWED": ErrorDefinition(405, False),
    "CONFLICT": ErrorDefinition(409, False),
    "GPU_BUDGET_EXCEEDED": ErrorDefinition(409, False),
    "MODEL_UNAVAILABLE": ErrorDefinition(503, True),
    "MODEL_CAPABILITY_MISMATCH": ErrorDefinition(422, False),
    "UPSTREAM_TIMEOUT": ErrorDefinition(504, True),
    "UPSTREAM_ERROR": ErrorDefinition(502, True),
    "UPSTREAM_RESPONSE_INVALID": ErrorDefinition(502, True),
    "STRUCTURED_OUTPUT_INVALID": ErrorDefinition(502, True),
    "RATE_LIMITED": ErrorDefinition(429, True),
    "QUEUE_TIMEOUT": ErrorDefinition(503, True),
    "CIRCUIT_OPEN": ErrorDefinition(503, True),
    "REQUEST_TOO_LARGE": ErrorDefinition(413, False),
    "INTERNAL_ERROR": ErrorDefinition(500, False),
    "DETECTOR_DISABLED": ErrorDefinition(409, False),
    "STREAM_LIMIT_EXCEEDED": ErrorDefinition(None, False),
    "MAIN_MODEL_CONTROL_UNAVAILABLE": ErrorDefinition(503, True),
    "MAIN_MODEL_SWITCH_IN_PROGRESS": ErrorDefinition(503, True),
}

# 기존 소비처가 쓰는 조회 표이지만 값은 ERROR_DEFINITIONS에서만 파생된다.
ERROR_STATUS = {
    code: definition.status_code
    for code, definition in ERROR_DEFINITIONS.items()
    if definition.status_code is not None
}
ERROR_RETRYABLE = {
    code: definition.retryable for code, definition in ERROR_DEFINITIONS.items()
}

DIAGNOSTIC_VALUE_LIMIT = 2_000


def error_definition(code: str) -> ErrorDefinition:
    """등록되지 않은 공개 오류 코드를 조용히 다른 의미로 대체하지 않는다."""
    try:
        return ERROR_DEFINITIONS[code]
    except KeyError as exc:
        raise ValueError(f"unknown public error code: {code}") from exc

# 순수 HTTP status만 있을 때 사용할 기본 플랫폼 오류 코드. ``HTTPException``은
# status만 가지고 있으므로, 오류 핸들러는 그 status와 모순되지 않는 대표 코드가
# 필요하다 (이전에는 401이 아닌 모든 경우가 VALIDATION_ERROR로 뭉뚱그려져서,
# 예를 들어 404가 code=VALIDATION_ERROR로 반환되었다). 여러 코드로 매핑될 수
# 있는 status는 가장 일반적인 코드를 지정하며, 특정 코드를 발생시켜야 하는
# 핸들러는 대신 ServiceError를 사용해야 한다.
STATUS_DEFAULT_CODE = {
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "REQUEST_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_ERROR",
    503: "MODEL_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}


def default_code_for_status(status_code: int) -> str:
    """오류 코드가 없는 HTTP status를 상태와 맞는 플랫폼 오류 코드로 매핑한다."""
    if status_code in STATUS_DEFAULT_CODE:
        return STATUS_DEFAULT_CODE[status_code]
    return "INTERNAL_ERROR" if status_code >= 500 else "VALIDATION_ERROR"


def new_request_id() -> str:
    return f"req_{uuid4().hex}"


def request_id_from_headers(headers: Any) -> str | None:
    request_id = headers.get("x-request-id") if hasattr(headers, "get") else None
    if not isinstance(request_id, str):
        return None
    request_id = request_id.strip()
    if not request_id or len(request_id) > 128:
        return None
    return request_id


def request_id_for(request: Any) -> str:
    if not getattr(request.state, "request_id", None):
        request.state.request_id = request_id_from_headers(request.headers) or new_request_id()
    return request.state.request_id


def _bounded_diagnostic_string(value: Any) -> str:
    text = str(value)
    if len(text) <= DIAGNOSTIC_VALUE_LIMIT:
        return text
    return f"{text[:DIAGNOSTIC_VALUE_LIMIT]}... [truncated]"


def _bounded_diagnostic_value(value: Any) -> Any:
    if isinstance(value, str):
        return _bounded_diagnostic_string(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return _bounded_diagnostic_string(value)


def exception_diagnostics(exc: BaseException | None) -> dict[str, Any] | None:
    """원본 예외에서 내부 로그에 남길 길이 제한 진단 요약을 만든다."""
    if exc is None:
        return None
    return {
        "cause_type": type(exc).__name__,
        "cause_message": _bounded_diagnostic_string(exc),
    }


def service_error_diagnostics(exc: "ServiceError") -> dict[str, Any] | None:
    diagnostics = dict(exc.diagnostics or {})
    cause_diagnostics = exception_diagnostics(exc.__cause__)
    if cause_diagnostics:
        for key, value in cause_diagnostics.items():
            diagnostics.setdefault(key, value)
    if not diagnostics:
        return None
    return {
        str(key): _bounded_diagnostic_value(value)
        for key, value in diagnostics.items()
    }


def error_payload(
    code: str,
    message: str,
    request_id: str | None = None,
    *,
    param: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definition = error_definition(code)
    context = REQUEST_ERROR_CONTEXT.get()
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": definition.retryable,
        "request_id": (context or {}).get("request_id") or request_id or new_request_id(),
    }
    # ``param``은 문제가 된 요청 필드명을 담는다 (예: "response_format.json_schema"
    # vs "input_audio.format"). 이를 통해 클라이언트는 메시지를 파싱하지 않고도
    # 잘못된 출력 스펙과 잘못된 입력 데이터 포맷을 구분할 수 있다. 필드로
    # 특정되지 않는 경우에는 생략되어, 필드 출처가 없는 응답은 이전과 byte 단위로
    # 동일하게 유지된다.
    if param is not None:
        error["param"] = param
    # ``details``는 오류 code만으로 표현할 수 없는 구조화된 복구 정보를 담는다.
    # 현재는 관리자 런타임 제어의 GPU 예산 거부에서 ``plan.stop`` 등을 전달한다.
    # 문자열 message를 다시 파싱하는 대신 이 필드를 사용해야 한다.
    if details:
        error["details"] = details
    if context is not None:
        context.update(
            error_code=code,
            error_message=message,
            error_retryable=definition.retryable,
        )
    return {"error": error}


def retry_after_header(retry_after_seconds: float | None) -> dict[str, str] | None:
    """재시도 대기 시간이 있으면 ``Retry-After`` 헤더를 만들고, 없으면 ``None``을 반환한다.

    HTTP Retry-After is whole seconds, and always rounds up so a client never
    retries earlier than the hinted window (e.g. 0.4s -> "1", not "0").
    """
    if retry_after_seconds is None:
        return None
    return {"Retry-After": str(max(1, math.ceil(retry_after_seconds)))}


def error_response_headers(
    code: str,
    payload: dict[str, Any],
    retry_after_seconds: float | None = None,
    additional_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    # X-Request-Id는 응답과 내부 로그를 연결하고 X-Error-Code는 기존 client 계약을
    # 유지한다. 원인 메시지는 body의 message와 내부 request context에 각각 존재하므로
    # 외부 header를 내부 logging bus로 사용하지 않는다.
    headers = dict(additional_headers or {})
    headers.update({
        "X-Error-Code": code,
        "X-Request-Id": payload["error"]["request_id"],
    })
    retry_header = retry_after_header(retry_after_seconds)
    if retry_header:
        headers.update(retry_header)
    return headers


def error_response(
    code: str,
    message: str,
    request_id: str | None = None,
    *,
    param: str | None = None,
    retry_after_seconds: float | None = None,
    details: dict[str, Any] | None = None,
    additional_headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    payload = error_payload(code, message, request_id, param=param, details=details)
    resolved_status = error_definition(code).status_code
    if resolved_status is None:
        raise ValueError(f"{code} is not an HTTP error code")
    return JSONResponse(
        payload,
        status_code=resolved_status,
        headers=error_response_headers(code, payload, retry_after_seconds, additional_headers),
    )


@dataclass(frozen=True)
class ServiceError(Exception):
    """플랫폼 표준 오류. 필드는 불변이다.

    주의: frozen dataclass라서 파이썬 레벨의 ``exc.__traceback__ = tb`` 대입이
    ``FrozenInstanceError``로 터진다. 예외를 그냥 raise/전파하는 경로는 C 레벨에서
    traceback을 세팅하므로 문제가 없지만, generator 기반 context manager
    (``@contextmanager``/``@asynccontextmanager``)는 종료 시 그 대입을 수행한다.
    따라서 ServiceError가 통과할 수 있는 구간을 그런 context manager로 감싸면
    원래 오류가 ``FrozenInstanceError``에 가려진다 -- 그 구간에는 ``__aexit__``를
    직접 구현한 클래스형 context manager를 쓴다
    (``services/main_model_inflight.py`` 참고).
    """

    code: str
    message: str
    _: KW_ONLY
    request_id: str | None = None
    param: str | None = None
    # 내부 진단 context. API 응답에 직렬화하지 않고 request log에서 allowlist만 쓴다.
    diagnostics: dict[str, Any] | None = None
    diagnostic_code: str | None = None
    details: dict[str, Any] | None = None
    # 재시도 가능한 호출자가 재시도 전에 대기해야 할 초 단위 시간 (예: 남은
    # circuit-breaker 쿨다운, 또는 admission-queue 거부에 대한 고정 힌트).
    # JSON body가 아니라 HTTP Retry-After 헤더로 노출되므로, 클라이언트는
    # payload를 파싱하지 않고도 읽을 수 있는 transport 레벨 힌트로 남는다.
    retry_after_seconds: float | None = None

    @property
    def status_code(self) -> int:
        status_code = error_definition(self.code).status_code
        if status_code is None:
            raise ValueError(f"{self.code} is an SSE event code, not an HTTP error code")
        return status_code

    @property
    def retryable(self) -> bool:
        return error_definition(self.code).retryable

    @property
    def operational_code(self) -> str:
        return self.diagnostic_code or self.code

    def to_payload(self) -> dict[str, Any]:
        context = REQUEST_ERROR_CONTEXT.get()
        if context is not None:
            context["diagnostic_code"] = self.operational_code
            context["error_diagnostics"] = service_error_diagnostics(self)
        return error_payload(
            self.code,
            self.message,
            self.request_id,
            param=self.param,
            details=self.details,
        )
