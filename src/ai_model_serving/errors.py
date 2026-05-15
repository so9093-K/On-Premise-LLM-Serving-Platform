from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse

ERROR_STATUS = {
    "VALIDATION_ERROR": 422,
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "MODEL_UNAVAILABLE": 503,
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
}


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


def error_payload(code: str, message: str, retryable: bool, request_id: str | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "request_id": request_id or new_request_id(),
        }
    }


def error_response(
    code: str,
    message: str,
    retryable: bool,
    status_code: int | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        error_payload(code, message, retryable, request_id),
        status_code=status_code or ERROR_STATUS.get(code, 500),
    )


@dataclass(frozen=True)
class ServiceError(Exception):
    code: str
    message: str
    retryable: bool = False
    status_code: int | None = None
    request_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return error_payload(self.code, self.message, self.retryable, self.request_id)

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            self.to_payload(),
            status_code=self.status_code or ERROR_STATUS.get(self.code, 500),
        )


def http_exception_to_error(exc: HTTPException) -> JSONResponse:
    code = "UNAUTHORIZED" if exc.status_code == 401 else "VALIDATION_ERROR"
    return error_response(code, str(exc.detail), False, exc.status_code)
