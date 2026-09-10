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
    # final content는 없지만 parser가 분리한 reasoning과 length 종료가 남는다.
    return {
        "id": "chatcmpl_x",
        "object": "chat.completion",
        "created": 1,
        "model": "local-main",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": None, "reasoning": "still thinking"},
                "finish_reason": "length",
            }
        ],
    }


def test_reasoning_truncation_is_preserved_but_empty_truncation_is_rejected():
    payload = _truncated_reasoning_response()
    assert validate_chat_response(payload, expected_model="local-main") is payload

    payload["choices"][0]["message"].pop("reasoning")
    with pytest.raises(ServiceError) as excinfo:
        validate_chat_response(payload, expected_model="local-main")
    exc = excinfo.value
    assert exc.code == "UPSTREAM_RESPONSE_INVALID"
    assert "max_tokens" in exc.message
    assert "truncated" in exc.message

    payload = _truncated_reasoning_response()
    payload["choices"][0]["finish_reason"] = "stop"
    with pytest.raises(ServiceError) as excinfo:
        validate_chat_response(payload, expected_model="local-main")
    exc = excinfo.value
    assert exc.code == "UPSTREAM_RESPONSE_INVALID"
    # length가 아닌 finish에서 모델이 그냥 아무것도 안 돌려준 경우엔 truncation
    # 힌트가 없어야 한다.
    assert "max_tokens" not in exc.message


def test_invalid_upstream_response_shapes_are_not_request_errors():
    # upstream 최상위 JSON 타입 오류도 client 입력 오류(422)로 돌려보내면 안 된다.
    from ai_model_serving.contracts.embedding import validate_embedding_response
    from ai_model_serving.contracts.risk import validate_risk_response

    for validate in (
        lambda value: validate_chat_response(value, expected_model="local-main"),
        lambda value: validate_embedding_response(value, expected_model="local-embed"),
        validate_risk_response,
    ):
        with pytest.raises(ServiceError) as invalid:
            validate([])
        assert invalid.value.code == "UPSTREAM_RESPONSE_INVALID"
        assert invalid.value.status_code == 502


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

    method_error = client.post("/v1/models", headers=auth_headers())
    assert method_error.status_code == 405
    assert method_error.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert method_error.headers["allow"] == "GET"


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
