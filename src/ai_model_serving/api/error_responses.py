from __future__ import annotations

from fastapi.responses import JSONResponse

from ..errors import default_code_for_status, error_response
from ..services.sidecar_client import SidecarRequestError, SidecarUnavailableError


def sidecar_unavailable_response(exc: SidecarUnavailableError) -> JSONResponse:
    """관리 plane 연결 실패를 Gateway의 공개 오류 계약으로 변환한다."""
    return error_response(
        "MAIN_MODEL_CONTROL_UNAVAILABLE",
        str(exc),
        retry_after_seconds=5,
    )


def sidecar_request_error_response(exc: SidecarRequestError) -> JSONResponse:
    """Sidecar의 구조화된 작업 거부를 Gateway 표준 오류 envelope로 전달한다."""
    detail = exc.detail
    raw_detail = detail if isinstance(detail, dict) else {}
    specific_code = raw_detail.get("code") if isinstance(raw_detail.get("code"), str) else None
    message = raw_detail.get("message") if isinstance(raw_detail.get("message"), str) else str(detail)

    if exc.status_code == 503:
        # 연결 자체의 실패뿐 아니라 Sidecar가 준비되지 않아 돌려준 503도 같은
        # control-plane 복구 행동을 요구한다.
        code = "MAIN_MODEL_CONTROL_UNAVAILABLE"
    elif exc.status_code == 409:
        code = "GPU_BUDGET_EXCEEDED" if specific_code == "GPU_BUDGET_EXCEEDED" else "CONFLICT"
    else:
        code = default_code_for_status(exc.status_code)

    details: dict[str, object] = {}
    if specific_code and code != "GPU_BUDGET_EXCEEDED":
        details["reason"] = specific_code
    for key, value in raw_detail.items():
        if key not in {"code", "message"}:
            details[key] = value
    return error_response(code, message, details=details or None)
