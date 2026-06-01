from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_gateway_clients_build_embedding_clients_from_model_routes(monkeypatch):
    SpyVLLMClient.created_endpoints = []
    SpyVLLMClient.closed_endpoints = []
    monkeypatch.setattr(gateway_app_module, "VLLMClient", SpyVLLMClient)

    clients = GatewayClients(settings())

    assert set(clients.embedding_clients) == {"local-embed", "local-embed-ko"}
    assert clients.embedding_clients["local-embed"] is clients.runtime_clients_by_service_key["embedding"]
    assert clients.embedding_clients["local-embed-ko"] is clients.runtime_clients_by_service_key["embedding_ko"]
    created = [endpoint.logical_id for endpoint in SpyVLLMClient.created_endpoints]
    assert created.count("local-embed") == 1
    assert created.count("local-embed-ko") == 1


def test_gateway_clients_pick_up_new_embedding_route_without_code_change(monkeypatch):
    SpyVLLMClient.created_endpoints = []
    monkeypatch.setattr(gateway_app_module, "VLLMClient", SpyVLLMClient)
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

    clients = GatewayClients(cfg)

    assert "local-embed-extra" in clients.embedding_clients
    assert clients.embedding_clients["local-embed-extra"] is clients.runtime_clients_by_service_key["embedding_extra"]
    assert "local-embed-extra" in [endpoint.logical_id for endpoint in SpyVLLMClient.created_endpoints]


def test_gateway_clients_dedupe_runtime_clients_by_service_key(monkeypatch):
    SpyVLLMClient.created_endpoints = []
    monkeypatch.setattr(gateway_app_module, "VLLMClient", SpyVLLMClient)
    base = settings()
    alias_profile = EmbeddingProfile(
        model="local-embed-alias",
        service_key="embedding",
        upstream_model_id="google/embeddinggemma-300m",
        dimensions=(768, 512, 256, 128),
        default_dimensions=768,
        retrieval_enabled=True,
        score_modes=("dense_cosine",),
        request_parameter_policy=_PRODUCTION_EMBEDDING_POLICY,
    )
    cfg = _settings_with_embedding_routes(
        embedding_profiles={**base.embedding_profiles, "local-embed-alias": alias_profile},
        embedding_model_routes={**base.embedding_model_routes, "local-embed-alias": "embedding"},
    )

    clients = GatewayClients(cfg)

    assert clients.embedding_clients["local-embed-alias"] is clients.embedding_clients["local-embed"]
    created = [endpoint.logical_id for endpoint in SpyVLLMClient.created_endpoints]
    assert created.count("local-embed") == 1

