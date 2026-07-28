from __future__ import annotations

import dataclasses
import io
import json
import logging

from .helpers import *  # noqa: F401,F403


def test_embeddings_logs_token_usage_regardless_of_body_flag():
    clients = FakeGatewayClients()
    clients.embedding_clients["local-embed"].post_response["usage"] = {
        "prompt_tokens": 6,
        "total_tokens": 6,
    }

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("ai_model_serving.gateway")
    logger.addHandler(handler)
    try:
        client = TestClient(create_gateway_app(settings(), clients))
        response = client.post(
            "/v1/embeddings",
            headers=auth_headers(),
            json={"model": "local-embed", "input": "안녕하세요"},
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    completed = [r for r in lines if r.get("event") == "http_request_completed"]
    assert completed, f"no http_request_completed log record captured: {stream.getvalue()}"
    record = completed[-1]
    assert record["prompt_tokens"] == 6
    assert record["total_tokens"] == 6
    # embeddings에는 completion_tokens 개념이 없다 -- usage에 없는 필드는 안 실려야 한다.
    assert "completion_tokens" not in record
    assert "request_body" not in record


def test_embeddings_logs_input_preview_and_vector_summary_when_flag_enabled():
    # 임베딩 응답은 float 벡터라 원문을 그대로 로그에 남기지 않는다 -- 개수/차원/
    # 모델 요약(_embedding_response_summary)만 response_body에 실려야 한다.
    clients = FakeGatewayClients()
    cfg = dataclasses.replace(settings(), log_request_response_body=True)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("ai_model_serving.gateway")
    logger.addHandler(handler)
    try:
        client = TestClient(create_gateway_app(cfg, clients))
        response = client.post(
            "/v1/embeddings",
            headers=auth_headers(),
            json={"model": "local-embed", "input": "안녕하세요"},
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    completed = [r for r in lines if r.get("event") == "http_request_completed"]
    assert completed, f"no http_request_completed log record captured: {stream.getvalue()}"
    record = completed[-1]
    assert record["request_body"] == "안녕하세요"
    assert record["response_body"] == "1 embeddings returned, dim=768, model=local-embed"


def test_embeddings_omits_request_response_body_when_flag_disabled():
    clients = FakeGatewayClients()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("ai_model_serving.gateway")
    logger.addHandler(handler)
    try:
        client = TestClient(create_gateway_app(settings(), clients))
        response = client.post(
            "/v1/embeddings",
            headers=auth_headers(),
            json={"model": "local-embed", "input": ["안녕하세요"]},
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    completed = [r for r in lines if r.get("event") == "http_request_completed"]
    assert completed
    assert "request_body" not in completed[-1]
    assert "response_body" not in completed[-1]


def test_embedding_input_preview_joins_list_input():
    from ai_model_serving.api.routers.gateway_inference import (
        _embedding_input_preview,
        _embedding_response_summary,
    )

    assert _embedding_input_preview({"input": "안녕하세요"}) == "안녕하세요"
    assert _embedding_input_preview({"input": ["첫 문장", "두 번째 문장"]}) == "첫 문장 | 두 번째 문장"
    assert _embedding_input_preview({}) == ""

    summary = _embedding_response_summary(
        {
            "model": "local-embed-ko",
            "data": [
                {"embedding": [0.1] * 1024, "index": 0},
                {"embedding": [0.2] * 1024, "index": 1},
            ],
        }
    )
    assert summary == "2 embeddings returned, dim=1024, model=local-embed-ko"


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

