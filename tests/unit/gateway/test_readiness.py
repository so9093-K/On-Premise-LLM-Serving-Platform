from __future__ import annotations

from .helpers import *  # noqa: F401,F403
from ai_model_serving.services.runtime_state import RuntimeState

def test_gateway_health_ready_and_models():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    assert client.get("/health").json() == {"status": "ok", "service": "gateway"}
    ready = client.get("/ready").json()
    assert ready["status"] == "ready"
    assert ready["phase"] == "serving"
    assert ready["not_ready_dependencies"] == []
    models = client.get("/v1/models", headers=auth_headers()).json()
    by_id = {item["id"]: item for item in models["data"]}
    assert set(by_id) == {"local-main", "local-embed", "local-embed-ko", "risk-prompt"}
    assert by_id["local-main"]["request_parameters"]["temperature"] == {"type": "number", "min": 0, "max": 2}
    assert by_id["local-main"]["request_parameters"]["stream"] == {"type": "boolean"}
    assert by_id["local-embed"]["request_parameters"]["dimensions"]["enum"] == [768, 512, 256, 128]
    assert "retrieval_score" in by_id["local-embed"]["capabilities"]
    assert by_id["local-embed-ko"]["capabilities"] == [
        "embeddings",
        "retrieval_rerank",
        "retrieval_score",
    ]
    assert by_id["risk-prompt"]["request_parameters"] == {}
    assert by_id["risk-prompt"]["fixed_parameters"] == {"max_tokens": 1, "temperature": 0}


def test_gateway_ready_forwards_admin_token_to_risk_adapter_readiness():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(admin_settings(), clients))
    response = client.get("/ready", headers={"Authorization": "Bearer admin-test-key"})
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert clients.risk_adapter.last_path == "/ready"
    assert clients.risk_adapter.last_headers == {"authorization": "Bearer admin-test-key"}


def test_gateway_readiness_reflects_risk_adapter_body_status():
    clients = FakeGatewayClients()
    clients.risk_adapter = FakeRuntimeClient(
        get_response={
            "status": "not_ready",
            "service": "risk-adapter",
            "dependencies": [{"name": "risk_prompt_vllm", "status": "not_ready"}],
        },
        endpoint=RuntimeEndpoint("risk-adapter", "http://risk", "risk-adapter", 1),
    )
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["phase"] == "waiting_for_dependencies"
    assert body["not_ready_dependencies"] == ["risk_adapter"]
    assert {item["name"]: item["status"] for item in body["dependencies"]}["risk_adapter"] == "not_ready"
    risk_dependency = next(item for item in body["dependencies"] if item["name"] == "risk_adapter")
    assert risk_dependency["endpoint"] == "http://risk/ready"
    assert "risk_prompt_vllm" in risk_dependency["message"]


def test_gateway_readiness_503_when_embedding_ko_vllm_down():
    clients = FakeGatewayClients()
    embedding_ko = FakeRuntimeClient(
        ready=False,
        get_response={"error": "model not loaded"},
        endpoint=RuntimeEndpoint("local-embed-ko", "http://embed-ko/v1", "local-embed-ko", 1),
    )
    clients.embedding_clients["local-embed-ko"] = embedding_ko
    clients.runtime_clients_by_service_key["embedding_ko"] = embedding_ko
    clients.runtimes["embedding_ko"] = embedding_ko
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert "embedding_ko_vllm" in body["not_ready_dependencies"]
    assert "embedding_ko_vllm" in body["required_not_ready_dependencies"]
    assert "embedding_ko_vllm" not in body["optional_not_ready_dependencies"]


def test_gateway_readiness_treats_stopped_runtime_as_optional():
    clients = FakeGatewayClients()
    embedding_ko = FakeRuntimeClient(
        ready=False,
        get_response={"error": "model not loaded"},
        endpoint=RuntimeEndpoint("local-embed-ko", "http://embed-ko/v1", "local-embed-ko", 1),
    )
    clients.embedding_clients["local-embed-ko"] = embedding_ko
    clients.runtime_clients_by_service_key["embedding_ko"] = embedding_ko
    clients.runtimes["embedding_ko"] = embedding_ko
    asyncio.run(clients.runtime_state.set("embedding_ko", RuntimeState.stopped))
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert "embedding_ko_vllm" in body["not_ready_dependencies"]
    assert "embedding_ko_vllm" in body["optional_not_ready_dependencies"]
    assert "embedding_ko_vllm" not in body["required_not_ready_dependencies"]


def test_gateway_readiness_builds_embedding_probes_from_model_routes():
    cfg = settings()
    clients = FakeGatewayClients()

    body = asyncio.run(_readiness(clients, cfg))

    dependency_names = {item["name"] for item in body["dependencies"]}
    assert {"embedding_vllm", "embedding_ko_vllm"}.issubset(dependency_names)


def test_gateway_readiness_picks_up_new_embedding_route_without_code_change():
    base = settings()
    extra_endpoint = RuntimeEndpoint("local-embed-extra", "http://embed-extra/v1", "local-embed-extra", 1)
    extra_profile = EmbeddingProfile(
        model="local-embed-extra",
        service_key="embedding_extra",
        upstream_model_id="example/embed-extra",
        dimensions=(384,),
        default_dimensions=384,
        retrieval_enabled=True,
        score_modes=("dense_cosine",),
    )
    cfg = _settings_with_embedding_routes(
        embedding_profiles={**base.embedding_profiles, "local-embed-extra": extra_profile},
        embedding_model_routes={**base.embedding_model_routes, "local-embed-extra": "embedding_extra"},
        runtime_endpoints={"embedding_extra": extra_endpoint},
    )
    clients = FakeGatewayClients()
    clients.runtime_clients_by_service_key["embedding_extra"] = FakeRuntimeClient(
        endpoint=extra_endpoint,
    )

    body = asyncio.run(_readiness(clients, cfg))

    dependency_names = {item["name"] for item in body["dependencies"]}
    assert "embedding_extra_vllm" in dependency_names


def test_collect_readiness_response_matches_schema():
    schema = json.loads(Path("specs/schemas/readiness_response.schema.json").read_text())
    validator = Draft202012Validator(schema)

    required_client = FakeRuntimeClient(endpoint=RuntimeEndpoint("req", "http://req/v1", "req", 1))
    optional_client = FakeRuntimeClient(
        ready=False,
        get_response={"error": "not ready"},
        endpoint=RuntimeEndpoint("opt", "http://opt/v1", "opt", 1),
    )
    probes = [
        DependencyProbe("required_dep", required_client, "models", required=True),
        DependencyProbe("optional_dep", optional_client, "models", required=False),
    ]

    body = asyncio.run(collect_readiness(service="gateway", probes=probes))

    validator.validate(body)
    assert body["status"] == "ready"
    assert body["required_not_ready_dependencies"] == []
    assert body["optional_not_ready_dependencies"] == ["optional_dep"]
    assert body["not_ready_dependencies"] == ["optional_dep"]
