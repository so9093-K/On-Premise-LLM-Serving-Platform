from __future__ import annotations

from .helpers import *  # noqa: F401,F403

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

    unsupported_mime = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/tiff;base64,AA=="}}]}]},
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
