"""/v1/retrieval/score, /v1/retrieval/rerank의 dense retrieval 동작을 검증한다:
모델별 query/document prefix 정책, truncate_prompt_tokens 전달, top_n 제한,
기본 모델 선택(local-embed-ko), embedding 런타임 중지 시 503 처리."""

from __future__ import annotations

from .helpers import *  # noqa: F401,F403
from ai_model_serving.services.runtime_state import RuntimeState

def test_gateway_dense_retrieval_uses_embedding_runtime():
    clients = FakeGatewayClients()
    def embed_response(_path, payload, **_kwargs):
        vectors = {
            "task: search result | query: 검색어": [1.0] + [0.0] * 767,
            "title: none | text: 관련": [1.0] + [0.0] * 767,
            "title: none | text: 무관": [0.0, 1.0] + [0.0] * 766,
        }
        inputs = payload["input"]
        return {
            "object": "list",
            "model": "local-embed",
            "data": [{"object": "embedding", "embedding": vectors[text], "index": index} for index, text in enumerate(inputs)],
        }
    clients.embedding_clients["local-embed"].post_response = embed_response
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "검색어", "documents": ["관련", "무관"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["score_mode"] == "dense_cosine"
    assert body["scores"] == [{"index": 0, "score": 1.0}, {"index": 1, "score": 0.0}]
    assert clients.embedding_clients["local-embed"].last_path == "embeddings"


def test_gateway_retrieval_returns_503_when_embedding_runtime_stopped():
    clients = FakeGatewayClients()
    asyncio.run(clients.runtime_state.set("embedding", RuntimeState.stopped))
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "검색어", "documents": ["관련"]},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "MODEL_UNAVAILABLE"
    assert "embedding runtime is stopped" in body["error"]["message"]
    assert clients.embedding_clients["local-embed"].last_path is None


def test_gateway_retrieval_forwards_truncate_prompt_tokens_to_embedding_runtime():
    clients = FakeGatewayClients()
    captured_payloads: list[dict] = []

    def embed_response(_path, payload, **_kwargs):
        captured_payloads.append(payload)
        inputs = payload["input"]
        return {
            "object": "list",
            "model": "local-embed",
            "data": [
                {"object": "embedding", "embedding": [1.0] + [0.0] * 767, "index": index}
                for index, _text in enumerate(inputs)
            ],
        }

    clients.embedding_clients["local-embed"].post_response = embed_response
    client = TestClient(create_gateway_app(settings(), clients))

    score = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "q", "documents": ["d"], "truncate_prompt_tokens": 512},
    )
    rerank = client.post(
        "/v1/retrieval/rerank",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "q", "documents": ["d"], "truncate_prompt_tokens": 512},
    )

    assert score.status_code == 200
    assert rerank.status_code == 200
    assert captured_payloads
    assert all(payload.get("truncate_prompt_tokens") == 512 for payload in captured_payloads)
    assert all("truncation_side" not in payload for payload in captured_payloads)


def test_gateway_retrieval_rejects_truncation_side_before_upstream_call():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "q", "documents": ["d"], "truncation_side": "left"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "truncation_side" in response.json()["error"]["message"]
    assert clients.embedding_clients["local-embed"].last_path is None


def test_gateway_retrieval_truncate_prompt_tokens_can_change_ranking_canary():
    clients = FakeGatewayClients()

    def embed_response(_path, payload, **_kwargs):
        truncated = payload.get("truncate_prompt_tokens") == 1
        data = []
        for index, text in enumerate(payload["input"]):
            if text.startswith("task: search result | query: "):
                vector = [0.0, 1.0] if truncated else [1.0, 0.0]
            elif text.endswith("first"):
                vector = [1.0, 0.0]
            else:
                vector = [0.0, 1.0]
            data.append({"object": "embedding", "embedding": vector + [0.0] * 766, "index": index})
        return {"object": "list", "model": "local-embed", "data": data}

    clients.embedding_clients["local-embed"].post_response = embed_response
    client = TestClient(create_gateway_app(settings(), clients))
    base_payload = {"model": "local-embed", "query": "long query", "documents": ["first", "second"]}

    before = client.post("/v1/retrieval/rerank", headers=auth_headers(), json=base_payload)
    after = client.post(
        "/v1/retrieval/rerank",
        headers=auth_headers(),
        json={**base_payload, "truncate_prompt_tokens": 1},
    )

    assert before.status_code == 200
    assert after.status_code == 200
    assert [item["index"] for item in before.json()["results"]] == [0, 1]
    assert [item["index"] for item in after.json()["results"]] == [1, 0]


def test_gateway_rejects_retrieval_extra_fields_before_upstream_call():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    score = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "q", "documents": ["d"], "extra": "x"},
    )
    assert score.status_code == 422
    assert score.json()["error"]["code"] == "VALIDATION_ERROR"
    assert clients.embedding_clients["local-embed"].last_path is None

    token_embeddings = client.post(
        "/v1/retrieval/token-embeddings",
        headers=auth_headers(),
        json={"model": "local-embed-ko", "texts": ["문서"], "extra": "x"},
    )
    assert token_embeddings.status_code == 404


def test_gateway_dense_retrieval_rejects_invalid_embedding_upstream_response():
    clients = FakeGatewayClients()
    clients.embedding_clients["local-embed"].post_response = {"object": "list", "model": "local-embed", "data": []}
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "검색어", "documents": ["문서"]},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_SCHEMA_ERROR"


def test_gateway_rejects_unsupported_retrieval_model():
    # model/score_mode는 specs/schemas/retrieval_*_request.schema.json의 enum이
    # 스키마 레벨에서 먼저 걸러낸다 -- RetrievalService._resolve_retrieval_mode의
    # MODEL_CAPABILITY_MISMATCH 분기까지 도달하지 않는다.
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    for endpoint in ("/v1/retrieval/rerank", "/v1/retrieval/score"):
        response = client.post(
            endpoint,
            headers=auth_headers(),
            json={"model": "not-a-retrieval-model", "query": "test", "documents": ["doc"]},
        )
        assert response.status_code == 422, f"{endpoint} should reject an unknown model"
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert clients.embedding_clients["local-embed"].last_path is None
        assert clients.embedding_clients["local-embed-ko"].last_path is None


def test_gateway_rejects_unsupported_score_mode():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    for endpoint in ("/v1/retrieval/rerank", "/v1/retrieval/score"):
        response = client.post(
            endpoint,
            headers=auth_headers(),
            json={"score_mode": "not-a-score-mode", "query": "test", "documents": ["doc"]},
        )
        assert response.status_code == 422, f"{endpoint} should reject an unknown score_mode"
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_gateway_retrieval_default_model_is_embed_ko():
    clients = FakeGatewayClients()
    captured_inputs: list[list[str]] = []

    def embed_ko_response(_path, payload, **_kwargs):
        inputs = payload["input"] if isinstance(payload.get("input"), list) else [payload["input"]]
        captured_inputs.append(inputs)
        return {
            "object": "list",
            "model": "local-embed-ko",
            "data": [{"object": "embedding", "embedding": [0.1] * 1024, "index": i} for i in range(len(inputs))],
        }
    clients.embedding_clients["local-embed-ko"].post_response = embed_ko_response
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"query": "대한민국의 수도는?", "documents": ["서울은 대한민국의 수도이다.", "부산은 항구 도시이다."]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "local-embed-ko"
    assert body["score_mode"] == "dense_cosine"
    assert clients.embedding_clients["local-embed-ko"].last_path == "embeddings"
    assert clients.embedding_clients["local-embed"].last_path is None
    assert len(captured_inputs) == 2
    assert captured_inputs[0] == ["query: 대한민국의 수도는?"]
    assert captured_inputs[1] == ["서울은 대한민국의 수도이다.", "부산은 항구 도시이다."]


def test_gateway_local_embed_ko_retrieval_applies_query_prefix_only():
    """local-embed-ko retrieval query에만 'query: ' prefix가 붙고 document에는 붙지 않는다."""
    clients = FakeGatewayClients()
    captured_inputs: list[list[str]] = []

    def embed_ko_response(_path, payload, **_kwargs):
        inputs = payload["input"]
        captured_inputs.append(inputs)
        return {
            "object": "list",
            "model": "local-embed-ko",
            "data": [{"object": "embedding", "embedding": [0.1] * 1024, "index": i} for i in range(len(inputs))],
        }
    clients.embedding_clients["local-embed-ko"].post_response = embed_ko_response
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed-ko", "query": "대한민국의 수도는?", "documents": ["서울은 대한민국의 수도이다.", "부산은 항구 도시이다."]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "local-embed-ko"
    assert body["score_mode"] == "dense_cosine"
    assert clients.embedding_clients["local-embed"].last_path is None
    assert len(captured_inputs) == 2
    assert captured_inputs[0] == ["query: 대한민국의 수도는?"]
    assert captured_inputs[1] == ["서울은 대한민국의 수도이다.", "부산은 항구 도시이다."]


def test_gateway_local_embed_retrieval_applies_task_prefix_regression():
    """local-embed(EmbeddingGemma) prompt policy 불변: query는 task prefix, document는 title prefix."""
    clients = FakeGatewayClients()
    captured_inputs: list[list[str]] = []

    def embed_response(_path, payload, **_kwargs):
        inputs = payload["input"]
        captured_inputs.append(inputs)
        return {
            "object": "list",
            "model": "local-embed",
            "data": [{"object": "embedding", "embedding": [0.1] * 768, "index": i} for i in range(len(inputs))],
        }
    clients.embedding_clients["local-embed"].post_response = embed_response
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed", "query": "대한민국 수도는?", "documents": ["서울은 대한민국의 수도이다."]},
    )

    assert response.status_code == 200
    assert clients.embedding_clients["local-embed-ko"].last_path is None
    assert len(captured_inputs) == 2
    assert captured_inputs[0] == ["task: search result | query: 대한민국 수도는?"]
    assert captured_inputs[1] == ["title: none | text: 서울은 대한민국의 수도이다."]


def test_gateway_rerank_top_n_limits_results():
    """rerank top_n이 결과 개수를 제한하는지 확인한다."""
    clients = FakeGatewayClients()
    vectors = {
        "query: 쿼리": [1.0] + [0.0] * 1023,
        "d0": [0.1, 0.995] + [0.0] * 1022,
        "d1": [1.0] + [0.0] * 1023,
        "d2": [0.5, 0.866] + [0.0] * 1022,
    }
    clients.embedding_clients["local-embed-ko"].post_response = lambda _path, payload, **_kwargs: {
        "object": "list",
        "model": "local-embed-ko",
        "data": [{"object": "embedding", "embedding": vectors[text], "index": index} for index, text in enumerate(payload["input"])],
    }
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/retrieval/rerank",
        headers=auth_headers(),
        json={"model": "local-embed-ko", "query": "쿼리", "documents": ["d0", "d1", "d2"], "top_n": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["results"]) == 2
    assert [item["index"] for item in body["results"]] == [1, 2]


def test_gateway_score_rejects_top_n():
    """score endpoint에서 top_n을 보내면 422를 반환한다."""
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json={"model": "local-embed-ko", "query": "쿼리", "documents": ["문서"], "top_n": 1},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert clients.embedding_clients["local-embed-ko"].last_path is None
