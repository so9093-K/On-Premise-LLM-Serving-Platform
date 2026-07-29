from __future__ import annotations

import dataclasses
import io
import json
import logging

from starlette.requests import Request

from ai_model_serving.logging_policy import record_token_usage, safe_request_log_record

from .helpers import *  # noqa: F401,F403


def test_chat_completion_logs_masked_request_response_body_when_flag_enabled():
    # LOG_REQUEST_RESPONSE_BODY=true일 때 gateway_inference.py가 request.state에
    # 마스킹된 텍스트를 남기고, safe_request_logging_middleware가 이를 실제
    # http_request_completed 로그 레코드로 옮기는지 end-to-end로 검증한다.
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = (
        "연락처는 hong@example.com 입니다"
    )
    cfg = dataclasses.replace(settings(), log_request_response_body=True)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("ai_model_serving.gateway")
    logger.addHandler(handler)
    try:
        client = TestClient(create_gateway_app(cfg, clients))
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "local-main",
                "messages": [{"role": "user", "content": "제 이메일은 test@example.com 입니다"}],
            },
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    completed = [r for r in lines if r.get("event") == "http_request_completed"]
    assert completed, f"no http_request_completed log record captured: {stream.getvalue()}"
    record = completed[-1]
    assert "test@example.com" not in record["request_body"]
    assert "[EMAIL_ADDRESS]" in record["request_body"]
    assert "hong@example.com" not in record["response_body"]
    assert "[EMAIL_ADDRESS]" in record["response_body"]


def test_chat_completion_logs_token_usage_regardless_of_body_flag():
    # 토큰 개수는 프롬프트/응답 원문과 달리 민감정보가 아니라
    # LOG_REQUEST_RESPONSE_BODY와 무관하게 항상 로그에 실려야 한다(latency_ms와
    # 동급). 여기서는 플래그를 기본값(false)으로 둔 채로 확인한다.
    clients = FakeGatewayClients()
    clients.main_llm.post_response["usage"] = {
        "prompt_tokens": 12,
        "completion_tokens": 34,
        "total_tokens": 46,
    }

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("ai_model_serving.gateway")
    logger.addHandler(handler)
    try:
        client = TestClient(create_gateway_app(settings(), clients))
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    completed = [r for r in lines if r.get("event") == "http_request_completed"]
    assert completed, f"no http_request_completed log record captured: {stream.getvalue()}"
    record = completed[-1]
    assert record["prompt_tokens"] == 12
    assert record["completion_tokens"] == 34
    assert record["total_tokens"] == 46
    assert "request_body" not in record
    assert "response_body" not in record


def test_request_log_ignores_negative_or_boolean_token_usage():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
        }
    )

    record_token_usage(
        request,
        {"prompt_tokens": -1, "completion_tokens": True, "total_tokens": 3},
    )
    record = safe_request_log_record(
        service="gateway",
        request=request,
        status_code=200,
        elapsed_seconds=0.01,
    )

    assert record["total_tokens"] == 3
    assert "prompt_tokens" not in record
    assert "completion_tokens" not in record


def test_chat_completion_omits_request_response_body_when_flag_disabled():
    clients = FakeGatewayClients()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("ai_model_serving.gateway")
    logger.addHandler(handler)
    try:
        client = TestClient(create_gateway_app(settings(), clients))
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    completed = [r for r in lines if r.get("event") == "http_request_completed"]
    assert completed
    assert "request_body" not in completed[-1]
    assert "response_body" not in completed[-1]


def test_gateway_accepts_bounded_multimodal_chat_and_enforces_model_token_cap():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    multimodal = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe this image"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAAAAAAAA"}},
                    ],
                }
            ],
        },
    )
    assert multimodal.status_code == 200
    assert clients.main_llm.last_payload["messages"][0]["content"][1]["type"] == "image_url"

    remote_image = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}]},
    )
    assert remote_image.status_code == 422

    invalid_base64 = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,not-base64"}}]}]},
    )
    assert invalid_base64.status_code == 422

    supported_gif = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="}}]}]},
    )
    assert supported_gif.status_code == 200

    supported_bmp = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/bmp;base64,Qk1GAAAAAAAAADYAAAAoAAAAAQAAAAEAAAABABgAAAAAABAAAADEDgAAxA4AAAAAAAAAAAAA////AA=="}}]}]},
    )
    assert supported_bmp.status_code == 200

    supported_avif = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/avif;base64,AAAAGGZ0eXBhdmlmAAAAAGF2aWZtaWYxAAAAFGlzcGUAAAAAAAAAAQAAAAE="}}]}]},
    )
    assert supported_avif.status_code == 200

    supported_jp2 = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jp2;base64,AAAADGpQICANCocKAAAAFGZ0eXBqcDIgAAAAAGpwMiAAAAAeanAyaAAAABZpaGRyAAAAAQAAAAEAAwcHAAAAAA=="}}]}]},
    )
    assert supported_jp2.status_code == 200

    supported_tiff = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/tiff;base64,SUkqAAgAAAACAAABBAABAAAAAQAAAAEBBAABAAAAAQAAAAAAAAA="}}]}]},
    )
    assert supported_tiff.status_code == 200

    supported_x_tiff = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/x-tiff;base64,SUkqAAgAAAACAAABBAABAAAAAQAAAAEBBAABAAAAAQAAAAAAAAA="}}]}]},
    )
    assert supported_x_tiff.status_code == 200

    unsupported_mime = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/svg+xml;base64,AA=="}}]}]},
    )
    assert unsupported_mime.status_code == 422

    too_large_image = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAAAAAAAAAAAAAA"}}]}]},
    )
    assert too_large_image.status_code == 422


    oversized_dimensions = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAACAAAAAgACAIAAAAAAAAA"}}]}]},
    )
    assert oversized_dimensions.status_code == 422

    too_many = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 2048},
    )
    assert too_many.status_code == 422


def test_gateway_accepts_bounded_tool_calling_when_enabled():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(tool_calling_settings(), clients))
    payload = {
        "model": "local-main",
        "messages": [{"role": "user", "content": "서울 날씨를 확인해줘"}],
        "max_tokens": 32,
        "temperature": 0.2,
        "top_p": 0.9,
        "top_k": 40,
        "min_p": 0.0,
        "repetition_penalty": 1.05,
        "seed": 7,
        "n": 1,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather by city",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
    }
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=payload)
    assert response.status_code == 200
    assert clients.main_llm.last_payload["tools"][0]["function"]["name"] == "get_weather"

    parallel = dict(payload)
    parallel["parallel_tool_calls"] = True
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=parallel)
    assert response.status_code == 422

    multi_choice = dict(payload)
    multi_choice["n"] = 2
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=multi_choice)
    assert response.status_code == 422

    unknown = dict(payload)
    unknown["unknown_sampler"] = 1
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=unknown)
    assert response.status_code == 422

    message_unknown = dict(payload)
    message_unknown["messages"] = [{"role": "user", "content": "hello", "cache_hint": "unsafe"}]
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=message_unknown)
    assert response.status_code == 422

    part_unknown = dict(payload)
    part_unknown["messages"] = [{"role": "user", "content": [{"type": "text", "text": "hello", "extra": True}]}]
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=part_unknown)
    assert response.status_code == 422

    tool_unknown = dict(payload)
    tool_unknown["tools"] = [{"type": "function", "function": {"name": "get_weather", "x-extra": 1}}]
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=tool_unknown)
    assert response.status_code == 422

    choice_without_tools = dict(payload)
    choice_without_tools.pop("tools")
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=choice_without_tools)
    assert response.status_code == 422

    choice_mismatch = dict(payload)
    choice_mismatch["tool_choice"] = {"type": "function", "function": {"name": "lookup_stock"}}
    response = client.post("/v1/chat/completions", headers=auth_headers(), json=choice_mismatch)
    assert response.status_code == 422


def test_gateway_maps_reasoning_opt_in_to_vllm_chat_template_kwargs():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(tool_calling_settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "분석해줘"}],
            "reasoning": True,
            "max_tokens": 64,
        },
    )

    assert response.status_code == 200
    assert "reasoning" not in clients.main_llm.last_payload
    assert clients.main_llm.last_payload["chat_template_kwargs"] == {"enable_thinking": True}

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning": False,
        },
    )
    assert response.status_code == 200
    assert "reasoning" not in clients.main_llm.last_payload
    assert "chat_template_kwargs" not in clients.main_llm.last_payload

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning": "yes",
        },
    )
    assert response.status_code == 422


def test_gateway_allows_direct_local_self_ref_without_request_rejection():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"self\":null}"
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))
    self_ref_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "self": {
                "anyOf": [
                    {"$ref": "#"},
                    {"type": "null"},
                ]
            }
        },
        "required": ["self"],
    }

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "Return JSON."}],
            "response_format": _json_schema_format(self_ref_schema),
        },
    )

    assert response.status_code == 200


def test_gateway_allows_schema_annotation_defs_and_non_recursive_local_ref():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"x\":\"ok\"}"
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {"x": {"$ref": "#/$defs/value"}},
        "required": ["x"],
        "$defs": {"value": {"type": "string"}},
    }

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "Return JSON."}],
            "response_format": _json_schema_format(schema),
        },
    )

    assert response.status_code == 200


def test_gateway_allows_advanced_combinations_and_models_projection():
    clients = FakeGatewayClients()
    clients.main_llm.post_response = {
        "id": "chatcmpl_tool",
        "object": "chat.completion",
        "created": 1,
        "model": "local-main",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}],
    }
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))
    base = {
        "model": "local-main",
        "messages": [{"role": "user", "content": "Return JSON."}],
        "response_format": _json_schema_format(),
        "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
        "tool_choice": "auto",
    }
    assert client.post("/v1/chat/completions", headers=auth_headers(), json=base).status_code == 200
    assert client.post("/v1/chat/completions", headers=auth_headers(), json={**base, "reasoning": True}).status_code == 200
    assert "reasoning" not in clients.main_llm.last_payload
    assert clients.main_llm.last_payload["chat_template_kwargs"] == {"enable_thinking": True}
    assert client.post("/v1/chat/completions", headers=auth_headers(), json={**base, "logit_bias": {"42": 1}}).status_code == 200

    models = client.get("/v1/models", headers=auth_headers()).json()["data"][0]["request_parameters"]
    assert {"response_format", "logprobs", "top_logprobs", "logit_bias"}.issubset(models)
