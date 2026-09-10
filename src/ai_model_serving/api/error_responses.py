from __future__ import annotations

from fastapi.responses import JSONResponse

from ..errors import default_code_for_status, error_response, exception_diagnostics
from ..logging_policy import record_suppressed_error_cause
from ..services.sidecar_client import SidecarRequestError, SidecarUnavailableError

_CONTROL_UNAVAILABLE_MESSAGE = "Main model control service is temporarily unavailable."


def sidecar_unavailable_response(exc: SidecarUnavailableError) -> JSONResponse:
    """관리 plane 연결 실패를 Gateway의 공개 오류 계약으로 변환한다.

    ``str(exc)``를 공개 message로 쓰지 않는다. SidecarUnavailableError의 문자열에는
    transport 예외 원문과 sidecar 응답 본문이 들어가는데, 이 helper는 관리 API만이
    아니라 공개 ``/v1/chat/completions``에서도 쓰인다. 실제로 내부 hostname,
    main-model 상태 파일 경로, Docker socket 경로가 공개 응답으로 나갔다.

    플랫폼의 일반 예외 처리기(app_kernel.unhandled_error_handler)가 이미 같은
    원칙을 쓴다 -- 공개 응답은 code/고정 message/request_id, 원인은 내부 로그.
    여기만 그 원칙에서 벗어나 있었다.
    """
    response = error_response(
        "MAIN_MODEL_CONTROL_UNAVAILABLE",
        _CONTROL_UNAVAILABLE_MESSAGE,
        retry_after_seconds=5,
    )
    record_suppressed_error_cause(
        code="MAIN_MODEL_CONTROL_UNAVAILABLE",
        diagnostics=exception_diagnostics(exc) or {},
    )
    return response


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
