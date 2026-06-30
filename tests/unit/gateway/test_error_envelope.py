from __future__ import annotations

import pytest

from ai_model_serving.contracts.embedding import validate_embedding_request
from ai_model_serving.contracts.risk import read_risk_prompt

from .helpers import *  # noqa: F401,F403


def _truncated_reasoning_response() -> dict:
    # A reasoning generation that spent its whole budget on the thinking phase:
    # finish_reason="length" with no assistant content and no tool_calls.
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
    # The actionable hint must point at max_tokens so a bare retry is not the obvious step.
    assert "max_tokens" in exc.message
    assert "truncated" in exc.message


def test_empty_content_without_truncation_keeps_generic_message():
    payload = _truncated_reasoning_response()
    payload["choices"][0]["finish_reason"] = "stop"
    with pytest.raises(ServiceError) as excinfo:
        validate_chat_response(payload, expected_model="local-main")
    exc = excinfo.value
    assert exc.code == "UPSTREAM_SCHEMA_ERROR"
    # No truncation hint when the model simply returned nothing for a non-length finish.
    assert "max_tokens" not in exc.message


def test_unknown_route_returns_platform_error_envelope():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    response = client.get("/v1/does-not-exist", headers=auth_headers())
    assert response.status_code == 404
    body = response.json()
    # Unmatched routes must carry the platform envelope (code + request_id), not
    # Starlette's bare {"detail": "Not Found"}.
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
    # The byte offset of the syntax error must not be surfaced as a field param.
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


@pytest.mark.parametrize("payload", [{"input": "hi"}, {"prompt": "  "}, {}])
def test_risk_prompt_errors_carry_prompt_param(payload):
    with pytest.raises(ServiceError) as excinfo:
        read_risk_prompt(payload)
    exc = excinfo.value
    assert exc.code == "VALIDATION_ERROR"
    assert exc.param == "prompt"
