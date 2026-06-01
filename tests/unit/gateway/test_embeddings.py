from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_gateway_forwards_chat_and_embeddings_to_vllm_paths():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    chat = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert chat.status_code == 200
    assert clients.main_llm.last_path == "chat/completions"
    embed = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "dimensions": 768},
    )
    assert embed.status_code == 200
    assert clients.embedding_clients["local-embed"].last_path == "embeddings"


def test_gateway_token_embeddings_route_removed():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    response = client.post(
        "/v1/retrieval/token-embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "texts": ["문서"]},
    )
    assert response.status_code == 404


def test_gateway_embeddings_does_not_apply_prompt_policy():
    """/v1/embeddings는 prompt policy를 적용하지 않는다 — local-embed-ko 직접 호출 시 prefix 없음."""
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed-ko", "input": ["임베딩할 텍스트 예시입니다."]},
    )

    assert response.status_code == 200
    assert clients.embedding_clients["local-embed-ko"].last_payload["input"] == ["임베딩할 텍스트 예시입니다."]
    assert clients.embedding_clients["local-embed-ko"].last_payload["input"] != ["query: 임베딩할 텍스트 예시입니다."]


def test_gateway_rejects_embedding_upstream_count_mismatch():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello", "world"]},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"


def test_gateway_rejects_embedding_upstream_dimension_mismatch():
    clients = FakeGatewayClients()
    clients.embedding_clients["local-embed"].post_response = {
        "object": "list",
        "model": "local-embed",
        "data": [{"object": "embedding", "embedding": [0.1], "index": 0}],
    }
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "dimensions": 768},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"


def test_gateway_rejects_embedding_upstream_index_mismatch():
    clients = FakeGatewayClients()
    clients.embedding_clients["local-embed"].post_response = {
        "object": "list",
        "model": "local-embed",
        "data": [{"object": "embedding", "embedding": [0.1] * 768, "index": 1}],
    }
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "dimensions": 768},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"


def test_gateway_embeddings_accepts_user_field():
    """production policy 기반 — user 필드는 허용되지만 upstream으로 전달되지 않는다."""
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(_settings_with_embedding_policy(_PRODUCTION_EMBEDDING_POLICY), clients))
    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "user": "test-user-id"},
    )
    assert response.status_code == 200
    assert clients.embedding_clients["local-embed"].last_path == "embeddings"
    assert "user" not in clients.embedding_clients["local-embed"].last_payload


def test_gateway_embeddings_user_field_not_sent_upstream():
    """user 필드는 allow_unlisted=false 환경에서도 허용되고 upstream에서 제거된다."""
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(_settings_with_embedding_policy(_PRODUCTION_EMBEDDING_POLICY), clients))
    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": "hello", "user": "abc"},
    )
    assert response.status_code == 200
    assert clients.embedding_clients["local-embed"].last_payload.get("user") is None
    assert clients.embedding_clients["local-embed"].last_payload.get("input") == "hello"


def test_gateway_embeddings_rejects_unsupported_encoding_format():
    """base64 encoding_format은 422를 반환한다 (smoke 기준 미지원)."""
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "encoding_format": "base64"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert clients.embedding_clients["local-embed"].last_path is None

