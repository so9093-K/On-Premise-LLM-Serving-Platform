"""chat/embedding/risk 응답 검증이 error.param에 실제로 도움이 되는 필드명을
싣는지, 404/malformed JSON 같은 프레임워크 레벨 에러도 플랫폼 공통 에러
envelope(code+request_id)를 따르는지 검증한다."""

from __future__ import annotations

import pytest

from ai_model_serving.contracts.embedding import validate_embedding_request
from ai_model_serving.contracts.risk import read_risk_prompt

from .helpers import *  # noqa: F401,F403


def _truncated_reasoning_response() -> dict:
    # reasoning 생성이 예산을 전부 thinking 단계에 써버린 경우:
    # finish_reason="length"인데 assistant content도 tool_calls도 없다.
    return {
        "id": "chatcmpl_x",
        "object": "chat.completion",
        "created": 1,
        "model": "local-main",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": None}, "finish_reason": "length"}],
    }


def test_truncated_response_error_names_max_tokens_cause():
    with pytest.raises(ServiceError) as excinfo:
        validate_chat_response(_truncated_reasoning_response(), expected_model="local-main")
    exc = excinfo.value
    assert exc.code == "UPSTREAM_SCHEMA_ERROR"
    # 조치 힌트는 max_tokens를 가리켜야 한다 — 그래야 단순 재시도가 뻔한 다음
    # 스텝처럼 보이지 않는다.
    assert "max_tokens" in exc.message
    assert "truncated" in exc.message


def test_empty_content_without_truncation_keeps_generic_message():
    payload = _truncated_reasoning_response()
    payload["choices"][0]["finish_reason"] = "stop"
    with pytest.raises(ServiceError) as excinfo:
        validate_chat_response(payload, expected_model="local-main")
    exc = excinfo.value
    assert exc.code == "UPSTREAM_SCHEMA_ERROR"
    # length가 아닌 finish에서 모델이 그냥 아무것도 안 돌려준 경우엔 truncation
    # 힌트가 없어야 한다.
    assert "max_tokens" not in exc.message


def test_unknown_route_returns_platform_error_envelope():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    response = client.get("/v1/does-not-exist", headers=auth_headers())
    assert response.status_code == 404
    body = response.json()
    # 매칭 안 되는 라우트도 Starlette의 기본 {"detail": "Not Found"}가 아니라
    # platform envelope(code + request_id)를 실어야 한다.
    assert "error" in body
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["request_id"].startswith("req_")
    Draft202012Validator(error_schema()).validate(body)


def test_malformed_json_body_does_not_leak_offset_as_param():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    response = client.post(
        "/v1/chat/completions",
        headers={**auth_headers(), "Content-Type": "application/json"},
        content="{,,,}",
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    # 구문 오류의 byte offset이 field param으로 노출되면 안 된다.
    assert error.get("param") is None
    assert "not valid JSON" in error["message"]


@pytest.mark.parametrize(
    "payload, expected_param",
    [
        ({"model": "wrong", "input": "hi"}, "model"),
        ({"model": "local-embed", "input": []}, "input"),
        ({"model": "local-embed", "input": "hi", "dimensions": 99999}, "dimensions"),
        ({"model": "local-embed", "input": "hi", "encoding_format": "banana"}, "encoding_format"),
    ],
)
def test_embedding_validation_errors_carry_actionable_param(payload, expected_param):
    with pytest.raises(ServiceError) as excinfo:
        validate_embedding_request(payload, expected_model="local-embed")
    exc = excinfo.value
    assert exc.code == "VALIDATION_ERROR"
    assert exc.param == expected_param


# {"input": "hi"}는 별도 케이스로 두지 않는다: read_risk_prompt()는
# `set(payload) != {"prompt"}`라는 동일한 한 줄 분기를 타므로 {}와 결과가 같다.
@pytest.mark.parametrize("payload", [{"prompt": "  "}, {}])
def test_risk_prompt_errors_carry_prompt_param(payload):
    with pytest.raises(ServiceError) as excinfo:
        read_risk_prompt(payload)
    exc = excinfo.value
    assert exc.code == "VALIDATION_ERROR"
    assert exc.param == "prompt"
