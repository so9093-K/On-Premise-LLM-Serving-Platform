from __future__ import annotations

import pytest

from ai_model_serving.contracts.chat_request import validate_chat_request
from ai_model_serving.contracts.chat_response_format import _validate_response_format
from ai_model_serving.contracts.common import reject_unknown_fields
from ai_model_serving.contracts.media import _validate_input_audio
from ai_model_serving.errors import ServiceError, default_code_for_status, error_payload, service_error_debug

# Permissive policy so the response_format validator reaches a field-level rejection
# rather than the "not enabled" gate (either way param must name response_format).
_RF_POLICY = {"response_format": {"enabled": True, "types": ["text", "json_schema"]}}
_STRICT_CHAT_POLICY = {
    "allow_unlisted_parameters": False,
    "supported_parameters": ["stream", "stream_options", "max_tokens", "tools", "tool_choice"],
    "tool_calling": {"enabled": True, "max_tools": 2},
}


def _raise(fn) -> ServiceError:
    with pytest.raises(ServiceError) as excinfo:
        fn()
    return excinfo.value


def test_response_format_error_carries_response_format_param():
    exc = _raise(lambda: _validate_response_format({"type": "bogus"}, {"messages": []}, _RF_POLICY))
    assert exc.code == "VALIDATION_ERROR"
    assert exc.param is not None and exc.param.split(".")[0] == "response_format"


def test_input_audio_error_carries_input_audio_param():
    exc = _raise(
        lambda: _validate_input_audio(
            {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "xyz"}},
            allowed_audio_formats={"wav"},
            max_audio_bytes=1000,
        )
    )
    assert exc.code == "VALIDATION_ERROR"
    assert exc.param == "input_audio"


def test_wrong_response_format_and_wrong_data_format_are_distinguishable():
    """The reported feedback: a client could not tell a wrong output spec from a wrong
    input data format because both are VALIDATION_ERROR. error.param now separates them."""
    rf = _raise(lambda: _validate_response_format({"type": "bogus"}, {"messages": []}, _RF_POLICY))
    df = _raise(
        lambda: _validate_input_audio(
            {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "xyz"}},
            allowed_audio_formats={"wav"},
            max_audio_bytes=1000,
        )
    )
    assert rf.param.split(".")[0] == "response_format"
    assert df.param.split(".")[0] == "input_audio"
    assert rf.param != df.param


def test_response_format_json_schema_error_carries_schema_param():
    exc = _raise(
        lambda: validate_chat_request(
            {
                "model": "local-main",
                "messages": [{"role": "user", "content": "Return JSON."}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "answer",
                        "schema": {"type": "array"},
                    },
                },
            },
            expected_model="local-main",
            request_parameter_policy={
                "allow_unlisted_parameters": False,
                "supported_parameters": ["response_format"],
                "response_format": {"enabled": True, "types": ["json_schema"]},
            },
        )
    )

    assert exc.code == "VALIDATION_ERROR"
    assert exc.param == "response_format.json_schema.schema"


def test_reject_unknown_fields_sets_param_from_context():
    exc = _raise(lambda: reject_unknown_fields({"junk": 1}, {"data"}, "input_audio"))
    assert exc.param == "input_audio"


@pytest.mark.parametrize(
    ("payload", "param"),
    [
        ({"model": "wrong", "messages": [{"role": "user", "content": "hi"}]}, "model"),
        ({"model": "local-main"}, "messages"),
        ({"model": "local-main", "stream": "true", "messages": [{"role": "user", "content": "hi"}]}, "stream"),
        (
            {
                "model": "local-main",
                "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": "hi"}],
            },
            "stream_options",
        ),
        ({"model": "local-main", "max_tokens": 0, "messages": [{"role": "user", "content": "hi"}]}, "max_tokens"),
        ({"model": "local-main", "foo": 1, "messages": [{"role": "user", "content": "hi"}]}, "foo"),
        ({"model": "local-main", "messages": [{"role": "bad", "content": "hi"}]}, "messages[0].role"),
    ],
)
def test_chat_validation_errors_carry_actionable_param(payload, param):
    exc = _raise(
        lambda: validate_chat_request(
            payload,
            expected_model="local-main",
            max_output_tokens=1024,
            request_parameter_policy=_STRICT_CHAT_POLICY,
        )
    )
    assert exc.code == "VALIDATION_ERROR"
    assert exc.param == param


def test_chat_audio_disabled_error_points_to_audio_part():
    exc = _raise(
        lambda: validate_chat_request(
            {
                "model": "local-main",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "mp3"}},
                        ],
                    }
                ],
            },
            expected_model="local-main",
            allowed_input_modalities=("text", "image"),
            request_parameter_policy=_STRICT_CHAT_POLICY,
        )
    )
    assert exc.param == "input_audio"
    assert "active main model profile" in exc.message


def test_chat_tool_errors_carry_tool_param():
    exc = _raise(
        lambda: validate_chat_request(
            {
                "model": "local-main",
                "messages": [{"role": "user", "content": "hi"}],
                "tool_choice": {"type": "function", "function": {"name": "missing"}},
            },
            expected_model="local-main",
            request_parameter_policy=_STRICT_CHAT_POLICY,
        )
    )
    assert exc.param == "tool_choice"


def test_default_code_for_status_does_not_contradict_status():
    # Previously every non-401 HTTPException collapsed to VALIDATION_ERROR (a 404 body
    # said code=VALIDATION_ERROR). The code must now match the status.
    assert default_code_for_status(401) == "UNAUTHORIZED"
    assert default_code_for_status(403) == "FORBIDDEN"
    assert default_code_for_status(404) == "NOT_FOUND"
    assert default_code_for_status(413) == "REQUEST_TOO_LARGE"
    assert default_code_for_status(503) == "MODEL_UNAVAILABLE"
    assert default_code_for_status(504) == "UPSTREAM_TIMEOUT"
    assert default_code_for_status(404) != "VALIDATION_ERROR"


def test_status_default_code_is_consistent_with_error_status():
    # Every status->code default must round-trip: ERROR_STATUS[code] == status. Otherwise
    # a HTTPException would carry a code whose own canonical status differs from the one
    # actually returned.
    from ai_model_serving.errors import ERROR_STATUS, STATUS_DEFAULT_CODE

    for status, code in STATUS_DEFAULT_CODE.items():
        assert ERROR_STATUS.get(code) == status, f"{status} -> {code} but ERROR_STATUS[{code}]={ERROR_STATUS.get(code)}"


def test_platform_envelope_http_statuses_have_non_contradictory_default():
    # Only apps that install the platform error handlers (gateway, risk-adapter, and
    # their routers) map HTTPException via default_code_for_status. apps/admin_sidecar.py
    # is an internal service-token app on a plain FastAPI() with the default {"detail"}
    # shape, so its statuses (e.g. a legitimate 409 eviction conflict) are out of scope.
    import re
    from pathlib import Path

    from ai_model_serving.errors import ERROR_STATUS, default_code_for_status

    root = Path(__file__).resolve().parents[2] / "src" / "ai_model_serving"
    statuses = set()
    for path in root.rglob("*.py"):
        if path.name == "admin_sidecar.py":
            continue
        for match in re.finditer(r"HTTPException\(\s*(\d{3})", path.read_text(encoding="utf-8")):
            statuses.add(int(match.group(1)))
    for status in statuses:
        code = default_code_for_status(status)
        assert ERROR_STATUS.get(code) == status, (
            f"HTTPException({status}) maps to code {code} whose status is {ERROR_STATUS.get(code)}; "
            "add the status to STATUS_DEFAULT_CODE or use a status with a matching platform code"
        )


def test_error_payload_omits_param_when_absent_and_includes_when_present():
    without = error_payload("INTERNAL_ERROR", "x", False)["error"]
    assert "param" not in without
    with_param = error_payload("VALIDATION_ERROR", "x", False, param="input_audio.format")["error"]
    assert with_param["param"] == "input_audio.format"


def test_error_payload_includes_bounded_debug_when_present():
    payload = error_payload(
        "UPSTREAM_ERROR",
        "upstream failed",
        True,
        debug={"cause_type": "HTTPStatusError", "cause_message": "x" * 2100, "upstream_status": 400},
    )["error"]

    assert payload["debug"]["cause_type"] == "HTTPStatusError"
    assert payload["debug"]["upstream_status"] == 400
    assert payload["debug"]["cause_message"].endswith("... [truncated]")


def test_service_error_debug_uses_original_cause_when_available():
    captured: ServiceError | None = None
    try:
        try:
            raise ValueError("decoder failed")
        except ValueError as cause:
            raise ServiceError(
                "VALIDATION_ERROR",
                "media decode failed",
                False,
                422,
                debug={"upstream_status": 400},
            ) from cause
    except ServiceError as exc:
        captured = exc

    assert captured is not None
    exc = captured
    debug = service_error_debug(exc)
    assert debug == {
        "upstream_status": 400,
        "cause_type": "ValueError",
        "cause_message": "decoder failed",
    }


def test_unhandled_exception_response_carries_cause_in_debug():
    """INTERNAL_ERROR의 body.message는 여전히 고정 문자열이어야 한다(원문 예외를
    그대로 실으면 안 되는 이유는 error.debug에만 담는 이유와 같다). 대신
    error.debug에는 실제 원인이 담겨야 한다 -- 업스트림 실패에 이미 쓰던
    debug.upstream_body 패턴(upstream.py의 _upstream_response_debug)과 동일하게,
    원인을 조용히 버리지 않도록 한다.

    X-Error-Message 헤더(=접근 로그의 error_message 필드, 운영자만 보는 내부
    채널)는 body.message와 달리 원인을 그대로 실어야 한다 -- 안 그러면
    Grafana Request Log Explorer의 error_message 컬럼이 INTERNAL_ERROR에 대해
    항상 빈 껍데기("Internal server error.")만 보여준다.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ai_model_serving.app_kernel import install_exception_handlers
    from ai_model_serving.metrics import Metrics
    from ai_model_serving.service_logging import service_logger

    app = FastAPI()
    install_exception_handlers(app, metrics=Metrics("test"), logger=service_logger("test"))

    @app.get("/boom")
    def boom():
        raise ValueError("db pool exhausted")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    body = response.json()["error"]
    assert body["code"] == "INTERNAL_ERROR"
    assert body["message"] == "Internal server error."
    assert body["debug"] == {"cause_type": "ValueError", "cause_message": "db pool exhausted"}
    assert response.headers["x-error-message"] == "Internal server error. (ValueError: db pool exhausted)"
