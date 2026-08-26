from __future__ import annotations

import math
from dataclasses import KW_ONLY, dataclass
from typing import Any
from uuid import uuid4

from fastapi.responses import JSONResponse

ERROR_STATUS = {
    "VALIDATION_ERROR": 422,
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "GPU_BUDGET_EXCEEDED": 409,
    "MODEL_UNAVAILABLE": 503,
    "MODEL_CAPABILITY_MISMATCH": 422,
    "UPSTREAM_TIMEOUT": 504,
    "UPSTREAM_ERROR": 502,
    "UPSTREAM_SCHEMA_ERROR": 502,
    "RATE_LIMITED": 429,
    "QUEUE_TIMEOUT": 503,
    "CIRCUIT_OPEN": 503,
    "REQUEST_TOO_LARGE": 413,
    "RUNTIME_NOT_READY": 503,
    "PARSE_ERROR": 502,
    "INTERNAL_ERROR": 500,
    "DETECTOR_DISABLED": 410,
    "STREAM_LIMIT_EXCEEDED": 504,
    "MAIN_MODEL_CONTROL_UNAVAILABLE": 503,
    "MAIN_MODEL_SWITCH_IN_PROGRESS": 503,
}

# code -> retryable. 이 값은 code로 완전히 결정된다 -- 같은 code가 상황에 따라 다른
# retryable로 나가면 클라이언트의 재시도 판단이 code마다 달라져 계약이 무너진다.
# 그래서 호출 지점마다 손으로 적지 않고 여기서 유도한다(ERROR_STATUS와 같은 구조).
# configs/error_catalog.yaml이 같은 값을 사용자용 의미 레이어로 서술하며,
# make validate의 validate_common_error_codes가 양쪽 일치를 고정한다.
ERROR_RETRYABLE = {
    "VALIDATION_ERROR": False,
    "UNAUTHORIZED": False,
    "FORBIDDEN": False,
    "NOT_FOUND": False,
    "CONFLICT": False,
    "GPU_BUDGET_EXCEEDED": False,
    "MODEL_UNAVAILABLE": True,
    "MODEL_CAPABILITY_MISMATCH": False,
    "UPSTREAM_TIMEOUT": True,
    "UPSTREAM_ERROR": True,
    "UPSTREAM_SCHEMA_ERROR": True,
    "RATE_LIMITED": True,
    "QUEUE_TIMEOUT": True,
    "CIRCUIT_OPEN": True,
    "REQUEST_TOO_LARGE": False,
    "RUNTIME_NOT_READY": True,
    "PARSE_ERROR": True,
    "INTERNAL_ERROR": False,
    "DETECTOR_DISABLED": False,
    "STREAM_LIMIT_EXCEEDED": False,
    "MAIN_MODEL_CONTROL_UNAVAILABLE": True,
    "MAIN_MODEL_SWITCH_IN_PROGRESS": True,
}

DEBUG_VALUE_LIMIT = 2_000

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
    409: "CONFLICT",
    410: "DETECTOR_DISABLED",
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


def _bounded_debug_string(value: Any) -> str:
    text = str(value)
    if len(text) <= DEBUG_VALUE_LIMIT:
        return text
    return f"{text[:DEBUG_VALUE_LIMIT]}... [truncated]"


def _bounded_debug_value(value: Any) -> Any:
    if isinstance(value, str):
        return _bounded_debug_string(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return _bounded_debug_string(value)


def exception_debug(exc: BaseException | None) -> dict[str, Any] | None:
    """원본 예외에서 응답에 노출해도 되는 길이 제한 요약을 반환한다."""
    if exc is None:
        return None
    return {
        "cause_type": type(exc).__name__,
        "cause_message": _bounded_debug_string(exc),
    }


def service_error_debug(exc: "ServiceError") -> dict[str, Any] | None:
    debug = dict(exc.debug or {})
    cause_debug = exception_debug(exc.__cause__)
    if cause_debug:
        for key, value in cause_debug.items():
            debug.setdefault(key, value)
    if not debug:
        return None
    return {str(key): _bounded_debug_value(value) for key, value in debug.items()}


def error_payload(
    code: str,
    message: str,
    retryable: bool,
    request_id: str | None = None,
    param: str | None = None,
    debug: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
        "request_id": request_id or new_request_id(),
    }
    # ``param``은 문제가 된 요청 필드명을 담는다 (예: "response_format.json_schema"
    # vs "input_audio.format"). 이를 통해 클라이언트는 메시지를 파싱하지 않고도
    # 잘못된 출력 스펙과 잘못된 입력 데이터 포맷을 구분할 수 있다. 필드로
    # 특정되지 않는 경우에는 생략되어, 필드 출처가 없는 응답은 이전과 byte 단위로
    # 동일하게 유지된다.
    if param is not None:
        error["param"] = param
    if debug:
        error["debug"] = {str(key): _bounded_debug_value(value) for key, value in debug.items()}
    # ``details``는 오류 code만으로 표현할 수 없는 구조화된 복구 정보를 담는다.
    # 현재는 관리자 런타임 제어의 GPU 예산 거부에서 ``plan.stop`` 등을 전달한다.
    # 문자열 message를 다시 파싱하는 대신 이 필드를 사용해야 한다.
    if details:
        error["details"] = details
    return {"error": error}


def retry_after_header(retry_after_seconds: float | None) -> dict[str, str] | None:
    """재시도 대기 시간이 있으면 ``Retry-After`` 헤더를 만들고, 없으면 ``None``을 반환한다.

    HTTP Retry-After is whole seconds, and always rounds up so a client never
    retries earlier than the hinted window (e.g. 0.4s -> "1", not "0").
    """
    if retry_after_seconds is None:
        return None
    return {"Retry-After": str(max(1, math.ceil(retry_after_seconds)))}


ERROR_MESSAGE_HEADER_LIMIT = 500


def _header_safe_message(message: str) -> str:
    # message에는 클라이언트가 보낸 값이 그대로 섞여 들어갈 수 있다(예: 잘못된
    # response_format.type 값 자체). 이걸 그대로 응답 헤더에 실으면 클라이언트가
    # CRLF를 주입해 헤더를 추가로 밀어넣을 수 있으므로, 출력 가능한 ASCII만 남기고
    # 나머지는 전부 버린 뒤 적당한 길이로 자른다.
    safe = "".join(ch for ch in message if " " <= ch <= "~")
    return safe[:ERROR_MESSAGE_HEADER_LIMIT]


def error_response_headers(
    code: str,
    payload: dict[str, Any],
    retry_after_seconds: float | None = None,
) -> dict[str, str]:
    # 클라이언트가 x-request-id를 안 보낸 요청은 error_payload가 request_id를
    # 새로 발급하는데, 그 값을 응답 헤더로도 에코해야 접근 로그(logging_policy.py)가
    # 같은 request_id를 남길 수 있다 — 안 그러면 로그의 request_id는 항상 None이라
    # 클라이언트가 보고한 request_id로는 로그를 절대 못 찾는다. X-Error-Code는
    # status만으로는 구분 안 되는 code(예: 503 하나에 MODEL_UNAVAILABLE/QUEUE_TIMEOUT/
    # CIRCUIT_OPEN 등 여러 code가 몰림)를 접근 로그에서 바로 구분하기 위함이다.
    # X-Error-Message는 code만으로는 안 보이는 실제 원인 설명(예: 어떤 필드가 왜
    # 잘못됐는지)까지 로그에서 바로 읽을 수 있게 한다 — code 하나만 봐서는 "무슨
    # 종류의 에러인지"는 알아도 "정확히 뭐가 잘못됐는지"는 여전히 서버에 들어가서
    # 찾아야 했다. INTERNAL_ERROR는 message 자체가 고정 문자열("Internal server
    # error.")이라 이 목적에 못 쓴다 -- 클라이언트에게는 그대로 두되(원문 예외를
    # message/헤더에 그대로 실으면 안 되는 이유는 error.debug에만 담는 이유와 같다),
    # 접근 로그(운영자만 보는 내부 채널)에는 debug.cause_message를 대신 실어서
    # 다른 error code들과 동일하게 로그만 보고 원인을 알 수 있게 한다.
    message = payload["error"]["message"]
    if code == "INTERNAL_ERROR":
        debug = payload["error"].get("debug") or {}
        cause_message = debug.get("cause_message")
        if cause_message:
            message = f"{message} ({debug['cause_type']}: {cause_message})"
    headers = {
        "X-Error-Code": code,
        "X-Request-Id": payload["error"]["request_id"],
        "X-Error-Message": _header_safe_message(message),
    }
    retry_header = retry_after_header(retry_after_seconds)
    if retry_header:
        headers.update(retry_header)
    return headers


def error_response(
    code: str,
    message: str,
    retryable: bool,
    status_code: int | None = None,
    request_id: str | None = None,
    param: str | None = None,
    debug: dict[str, Any] | None = None,
    retry_after_seconds: float | None = None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    payload = error_payload(code, message, retryable, request_id, param, debug, details)
    return JSONResponse(
        payload,
        status_code=status_code or ERROR_STATUS.get(code, 500),
        headers=error_response_headers(code, payload, retry_after_seconds),
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
    debug: dict[str, Any] | None = None
    # 재시도 가능한 호출자가 재시도 전에 대기해야 할 초 단위 시간 (예: 남은
    # circuit-breaker 쿨다운, 또는 admission-queue 거부에 대한 고정 힌트).
    # JSON body가 아니라 HTTP Retry-After 헤더로 노출되므로, 클라이언트는
    # payload를 파싱하지 않고도 읽을 수 있는 transport 레벨 힌트로 남는다.
    retry_after_seconds: float | None = None

    @property
    def status_code(self) -> int:
        # status도 retryable과 같이 code로 결정된다(ERROR_STATUS). 호출 지점에서 따로
        # 받던 시절엔 215곳 전부가 ERROR_STATUS와 같은 값을 손으로 적고 있었다.
        return ERROR_STATUS.get(self.code, 500)

    @property
    def retryable(self) -> bool:
        # 카탈로그에 없는 code는 validate가 잡는다. 런타임에서는 "재시도하지 말라"가
        # 안전한 기본값이라 False로 떨어뜨린다.
        return ERROR_RETRYABLE.get(self.code, False)

    def to_payload(self) -> dict[str, Any]:
        return error_payload(
            self.code,
            self.message,
            self.retryable,
            self.request_id,
            self.param,
            service_error_debug(self),
        )
