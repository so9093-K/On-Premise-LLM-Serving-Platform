"""VALIDATION_ERROR가 어떤 필드 때문인지(error.param) 실제로 구분되는지, HTTP
상태 코드와 error.code 기본 매핑이 서로 모순되지 않는지, error.debug/헤더에
원인이 안전하게(CRLF 주입 없이) 실리는지 검증한다."""

from __future__ import annotations

import pytest

from ai_model_serving.contracts.chat_request import validate_chat_request
from ai_model_serving.errors import ServiceError, default_code_for_status, error_payload, service_error_debug

_STRICT_CHAT_POLICY = {
    "allow_unlisted_parameters": False,
    "supported_parameters": ["stream", "stream_options", "max_tokens", "tools", "tool_choice"],
    "tool_calling": {"enabled": True, "max_tools": 2},
}


def _raise(fn) -> ServiceError:
    with pytest.raises(ServiceError) as excinfo:
        fn()
    return excinfo.value


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


# status 하나에 code가 여러 개 매달린 경우, 그중 무엇이 "맨몸 HTTPException"의
# 기본값이어야 하는지는 아래 왕복 불변식으로는 결정되지 않는다 -- 후보들이 전부
# 같은 status를 갖기 때문이다. 잘못 고르면 예컨대 bare 503이 CIRCUIT_OPEN으로 나가서
# 열리지도 않은 회로가 열렸다고 클라이언트에게 알린다. 그래서 모호한 status만
# 여기서 명시한다. 후보가 하나뿐인 status(401/403/404/413/...)는 왕복 불변식이
# 이미 답을 강제하므로 여기 적지 않는다 -- 적으면 ERROR_STATUS를 베껴 쓰는 것뿐이다.
AMBIGUOUS_STATUS_DEFAULTS = {
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    502: "UPSTREAM_ERROR",
    503: "MODEL_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}


def test_default_code_is_the_neutral_choice_when_a_status_has_several_codes():
    import collections

    from ai_model_serving.errors import ERROR_STATUS

    candidates = collections.defaultdict(set)
    for code, status in ERROR_STATUS.items():
        candidates[status].add(code)
    ambiguous = {status for status, codes in candidates.items() if len(codes) > 1}

    # 카탈로그가 자라면서 새로 모호해진 status를 알려준다 -- 그때 기본값을 무엇으로
    # 할지 결정해서 위 표에 적어야 한다. 조용히 통과시키면 아무도 안 고른 채로
    # 어떤 code가 기본값이 돼버린다.
    assert ambiguous == set(AMBIGUOUS_STATUS_DEFAULTS), (
        f"모호한 status 집합이 바뀌었다: {sorted(ambiguous)}"
    )
    for status, expected in AMBIGUOUS_STATUS_DEFAULTS.items():
        assert default_code_for_status(status) == expected


def test_status_default_code_is_consistent_with_error_status():
    # 모든 status->code 기본값은 왕복되어야 한다: ERROR_STATUS[code] == status.
    # 안 그러면 HTTPException이 실제로 반환한 status와 그 code의 정식 status가
    # 서로 다른 채로 나갈 수 있다.
    from ai_model_serving.errors import ERROR_STATUS, STATUS_DEFAULT_CODE

    for status, code in STATUS_DEFAULT_CODE.items():
        assert ERROR_STATUS.get(code) == status, f"{status} -> {code} but ERROR_STATUS[{code}]={ERROR_STATUS.get(code)}"


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


def test_error_payload_preserves_operation_details_without_message_parsing():
    payload = error_payload(
        "GPU_BUDGET_EXCEEDED",
        "GPU budget does not allow this activation.",
        False,
        details={"plan": {"stop": ["risk-prompt-vllm"]}},
    )["error"]

    assert payload["details"] == {"plan": {"stop": ["risk-prompt-vllm"]}}


def test_service_error_debug_uses_original_cause_when_available():
    captured: ServiceError | None = None
    try:
        try:
            raise ValueError("decoder failed")
        except ValueError as cause:
            raise ServiceError(
                "VALIDATION_ERROR", "media decode failed", debug={"upstream_status": 400},
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
    from tests.support.asgi import InlineASGITestClient as TestClient

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
