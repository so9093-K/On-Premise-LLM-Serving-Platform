"""response_format=json_object/json_schema 제약 디코딩 계약을 검증한다:
스키마 subset 제한(허용/금지 키워드), 재귀 $ref, 외부 $ref 거부, truncation
재시도 1회, 최대 깊이 제한 등 constrained decoding 관련 엣지 케이스 전체."""

from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_gateway_response_format_text_and_json_object_contracts():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))

    text = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], "response_format": {"type": "text"}},
    )
    assert text.status_code == 200
    assert clients.main_llm.last_payload["response_format"] == {"type": "text"}

    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"answer\":\"ok\"}"
    json_object = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": {"type": "json_object"}},
    )
    assert json_object.status_code == 200
    assert clients.main_llm.last_payload["response_format"] == {"type": "json_object"}

    missing_instruction = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], "response_format": {"type": "json_object"}},
    )
    assert missing_instruction.status_code == 422

    clients.main_llm.post_response["choices"][0]["message"]["content"] = "not json"
    invalid = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": {"type": "json_object"}},
    )
    assert invalid.status_code == 502
    assert invalid.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"


def test_gateway_json_schema_validation_and_subset_limits():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"answer\":\"ok\"}"
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))

    valid = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": _json_schema_format()},
    )
    assert valid.status_code == 200

    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"answer\":1}"
    invalid_response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": _json_schema_format()},
    )
    assert invalid_response.status_code == 502
    assert invalid_response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"

    bad_name = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": _json_schema_format(name="bad name")},
    )
    assert bad_name.status_code == 422

    disallowed = _json_schema_format({"type": "object", "additionalProperties": False, "allOf": []})
    assert client.post("/v1/chat/completions", headers=auth_headers(), json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": disallowed}).status_code == 422

    root_anyof = _json_schema_format({"type": "object", "additionalProperties": False, "anyOf": []})
    assert client.post("/v1/chat/completions", headers=auth_headers(), json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": root_anyof}).status_code == 422

    nested_anyof = _json_schema_format(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"value": {"anyOf": [{"type": "string"}, {"type": "number"}]}},
            "required": ["value"],
        }
    )
    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"value\":\"ok\"}"
    assert client.post("/v1/chat/completions", headers=auth_headers(), json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": nested_anyof}).status_code == 200

    missing_additional = _json_schema_format({"type": "object", "properties": {"answer": {"type": "string"}}})
    assert client.post("/v1/chat/completions", headers=auth_headers(), json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": missing_additional}).status_code == 422


def _chat_response(content: str) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "local-main",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
    }


def test_gateway_retries_once_on_truncated_structured_output_then_succeeds():
    call_count = 0

    def post_response(path, payload, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _chat_response('{"answer": "cut off mid ge')
        return _chat_response('{"answer": "ok"}')

    clients = FakeGatewayClients()
    clients.main_llm.post_response = post_response
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": _json_schema_format()},
    )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == '{"answer": "ok"}'
    assert call_count == 2


def test_gateway_gives_up_after_one_retry_on_repeated_truncation():
    call_count = 0

    def post_response(path, payload, **kwargs):
        nonlocal call_count
        call_count += 1
        return _chat_response('{"answer": "still cut off')

    clients = FakeGatewayClients()
    clients.main_llm.post_response = post_response
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "Return JSON."}], "response_format": _json_schema_format()},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"
    assert call_count == 2


def test_gateway_does_not_retry_truncation_without_structured_output():
    call_count = 0

    def post_response(path, payload, **kwargs):
        nonlocal call_count
        call_count += 1
        return {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "model": "local-main",
            "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": None}}],
        }

    clients = FakeGatewayClients()
    clients.main_llm.post_response = post_response
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"
    assert call_count == 1


def test_gateway_rejects_invalid_json_schema_request_shapes_and_requires_all_properties():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"answer\":\"ok\",\"note\":null}"
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))

    def post_schema(schema: dict) -> int:
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "local-main",
                "messages": [{"role": "user", "content": "Return JSON."}],
                "response_format": _json_schema_format(schema),
            },
        )
        return response.status_code

    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {"a": {"type": "string"}},
        "required": "a",
    }) == 422
    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": [],
        "required": [],
    }) == 422
    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {"answer": {"type": "string"}},
    }) == 422
    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {"answer": {"type": "string"}, "note": {"type": "string"}},
        "required": ["answer"],
    }) == 422
    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {"answer": {"type": "string"}},
        "required": ["answer", "extra"],
    }) == 422
    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {"answer": {"type": "string"}},
        "required": ["answer", 1],
    }) == 422
    assert post_schema({
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answer": {"type": "string"},
            "note": {"type": ["string", "null"]},
        },
        "required": ["answer", "note"],
    }) == 200


def test_gateway_allows_recursive_local_json_schema_refs():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = json.dumps(
        {"root": {"name": "parent", "children": [{"name": "child", "children": []}]}},
        separators=(",", ":"),
    )
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))
    recursive_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"root": {"$ref": "#/$defs/node"}},
        "required": ["root"],
        "$defs": {
            "node": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "children": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/node"},
                    },
                },
                "required": ["name", "children"],
            }
        },
    }

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "Return JSON."}],
            "response_format": _json_schema_format(recursive_schema),
        },
    )

    assert response.status_code == 200


def test_gateway_rejects_external_json_schema_refs():
    client = TestClient(create_gateway_app(advanced_chat_settings(), FakeGatewayClients()))
    rejected_refs = [
        "https://example.com/schema.json",
        "http://example.com/schema.json",
        "file:///tmp/schema.json",
        "schema.json",
        "/tmp/schema.json",
        123,
    ]

    for ref in rejected_refs:
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "local-main",
                "messages": [{"role": "user", "content": "Return JSON."}],
                "response_format": _json_schema_format(
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"external": {"$ref": ref}},
                        "required": ["external"],
                    }
                ),
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "local $ref" in body["error"]["message"]


def test_gateway_rejects_advanced_json_schema_reference_keywords():
    client = TestClient(create_gateway_app(advanced_chat_settings(), FakeGatewayClients()))

    cases = [
        (
            "$dynamicRef",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"$dynamicRef": "https://example.com/schema.json"}},
                "required": ["x"],
            },
        ),
        (
            "$dynamicRef",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"$dynamicRef": "#/$defs/x"}},
                "required": ["x"],
                "$defs": {"x": {"type": "string"}},
            },
        ),
        (
            "$recursiveRef",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"$recursiveRef": "https://example.com/schema.json"}},
                "required": ["x"],
            },
        ),
        (
            "$recursiveRef",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"$recursiveRef": "#"}},
                "required": ["x"],
            },
        ),
        (
            "$dynamicAnchor",
            {
                "type": "object",
                "additionalProperties": False,
                "$dynamicAnchor": "root",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        ),
        (
            "$recursiveAnchor",
            {
                "type": "object",
                "additionalProperties": False,
                "$recursiveAnchor": True,
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        ),
        (
            "$id",
            {
                "type": "object",
                "additionalProperties": False,
                "$id": "https://example.com/root",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        ),
        (
            "$anchor",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"x": {"type": "string", "$anchor": "x"}},
                "required": ["x"],
            },
        ),
    ]

    for keyword, schema in cases:
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "local-main",
                "messages": [{"role": "user", "content": "Return JSON."}],
                "response_format": _json_schema_format(schema),
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert keyword in body["error"]["message"]


def test_gateway_allows_disallowed_keyword_names_as_json_property_names():
    cases = [
        ("$id", {"$id": "value"}),
        ("not", {"not": "value"}),
        ("$dynamicRef", {"$dynamicRef": "value"}),
    ]

    for property_name, response_body in cases:
        clients = FakeGatewayClients()
        clients.main_llm.post_response["choices"][0]["message"]["content"] = json.dumps(response_body, separators=(",", ":"))
        client = TestClient(create_gateway_app(advanced_chat_settings(), clients))
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {property_name: {"type": "string"}},
            "required": [property_name],
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


def test_gateway_allows_disallowed_keyword_names_as_defs_names():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["message"]["content"] = "{\"x\":\"value\"}"
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"x": {"$ref": "#/$defs/$id"}},
        "required": ["x"],
        "$defs": {"$id": {"type": "string"}},
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


def test_gateway_still_rejects_disallowed_keywords_inside_property_schemas():
    client = TestClient(create_gateway_app(advanced_chat_settings(), FakeGatewayClients()))
    cases = [
        ("$id", {"type": "string", "$id": "https://example.com/nested"}),
        ("$anchor", {"type": "string", "$anchor": "x"}),
        ("$dynamicRef", {"$dynamicRef": "#/$defs/x"}),
        ("not", {"not": {"type": "string"}}),
        ("$ref", {"$ref": "https://example.com/schema.json"}),
    ]

    for keyword, property_schema in cases:
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(),
            json={
                "model": "local-main",
                "messages": [{"role": "user", "content": "Return JSON."}],
                "response_format": _json_schema_format(
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"x": property_schema},
                        "required": ["x"],
                    }
                ),
            },
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        if keyword == "$ref":
            assert "local $ref" in body["error"]["message"]
        else:
            assert keyword in body["error"]["message"]


def test_chat_response_validation_defensively_maps_invalid_expectation_schema():
    try:
        validate_chat_response(
            {
                "id": "chatcmpl_1",
                "object": "chat.completion",
                "created": 1,
                "model": "local-main",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "{\"answer\":\"ok\"}"}, "finish_reason": "stop"}],
            },
            expected_model="local-main",
            expectations=ChatResponseExpectations(
                response_format_type="json_schema",
                json_schema={"type": "object", "additionalProperties": False, "properties": [], "required": []},
                expect_logprobs=False,
                stream=False,
            ),
        )
    except ServiceError as exc:
        assert exc.code == "UPSTREAM_SCHEMA_ERROR"
        assert exc.status_code == 502
    else:
        raise AssertionError("invalid expectation schema should be mapped to UPSTREAM_SCHEMA_ERROR")


def test_chat_response_validation_defensively_maps_reference_resolution_errors():
    try:
        validate_chat_response(
            {
                "id": "chatcmpl_1",
                "object": "chat.completion",
                "created": 1,
                "model": "local-main",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "{\"answer\":\"ok\"}"}, "finish_reason": "stop"}],
            },
            expected_model="local-main",
            expectations=ChatResponseExpectations(
                response_format_type="json_schema",
                json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"answer": {"$ref": "#/$defs/missing"}},
                    "required": ["answer"],
                },
                expect_logprobs=False,
                stream=False,
            ),
        )
    except ServiceError as exc:
        assert exc.code == "UPSTREAM_SCHEMA_ERROR"
        assert exc.status_code == 502
        assert "could not be validated" in exc.message
    else:
        raise AssertionError("reference resolution errors should be mapped to UPSTREAM_SCHEMA_ERROR")


def _object_schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def test_gateway_max_depth_still_rejects_genuinely_excessive_nesting():
    schema = {"type": "string"}
    for _ in range(15):
        schema = _object_schema({"child": schema}, ["child"])

    client = TestClient(create_gateway_app(advanced_chat_settings(), FakeGatewayClients()))
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": _json_schema_format(schema, name="too_deep"),
        },
    )
    assert response.status_code == 422
    assert "depth" in response.json()["error"]["message"]

