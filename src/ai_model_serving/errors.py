from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi.responses import JSONResponse

ERROR_STATUS = {
    "VALIDATION_ERROR": 422,
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "MODEL_UNAVAILABLE": 503,
    "MODEL_PARKED": 503,
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
    "DETECTOR_RETIRED": 410,
    "DETECTOR_DISABLED": 410,
    "STREAM_LIMIT_EXCEEDED": 504,
    "MAIN_MODEL_CONTROL_UNAVAILABLE": 503,
    "MAIN_MODEL_SWITCH_IN_PROGRESS": 503,
}

DEBUG_VALUE_LIMIT = 2_000

# Default platform error code for a bare HTTP status. ``HTTPException`` carries only
# a status, so the error handler needs a representative code that does not contradict
# it (previously every non-401 collapsed to VALIDATION_ERROR, e.g. a 404 returned
# code=VALIDATION_ERROR). For statuses that map to several codes, this names the most
# general one; a handler raising a specific code should use ServiceError instead.
STATUS_DEFAULT_CODE = {
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
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
    """Map a bare HTTP status to a platform error code that matches the status."""
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
    """Return a bounded, response-safe summary of the original exception."""
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
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
        "request_id": request_id or new_request_id(),
    }
    # ``param`` names the offending request field (e.g. "response_format.json_schema"
    # vs "input_audio.format"), so a client can tell a wrong output spec from a wrong
    # input data format without parsing the message. Omitted when not field-scoped, so
    # responses without a field source stay byte-identical to before.
    if param is not None:
        error["param"] = param
    if debug:
        error["debug"] = {str(key): _bounded_debug_value(value) for key, value in debug.items()}
    return {"error": error}


def error_response(
    code: str,
    message: str,
    retryable: bool,
    status_code: int | None = None,
    request_id: str | None = None,
    param: str | None = None,
    debug: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        error_payload(code, message, retryable, request_id, param, debug),
        status_code=status_code or ERROR_STATUS.get(code, 500),
    )


@dataclass(frozen=True)
class ServiceError(Exception):
    code: str
    message: str
    retryable: bool = False
    status_code: int | None = None
    request_id: str | None = None
    param: str | None = None
    debug: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return error_payload(
            self.code,
            self.message,
            self.retryable,
            self.request_id,
            self.param,
            service_error_debug(self),
        )

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            self.to_payload(),
            status_code=self.status_code or ERROR_STATUS.get(self.code, 500),
        )
