from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_model_serving.apps.gateway import GatewayClients, create_gateway_app
from ai_model_serving.deployment_target import load_deployment_target
from ai_model_serving.settings import load_settings
from tests.support.asgi import InlineASGITestClient as TestClient
from tests.unit.gateway.helpers import FakeGatewayClients
from tests.unit.gateway.helpers import FakeRuntimeClient
from ai_model_serving.settings import RuntimeEndpoint


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "configs" / "deployment_targets.yaml"


def test_dynamic_target_preserves_existing_control_and_feature_contract() -> None:
    target = load_deployment_target(CATALOG, "linux-nvidia-dynamic")

    assert target.controllable is True
    assert target.internal_service_token_required is True
    assert {"chat", "embeddings", "retrieval", "risk", "runtime_control"} <= target.features


def test_static_target_is_main_only_and_externally_owned() -> None:
    target = load_deployment_target(CATALOG, "linux-nvidia-static")

    assert target.controllable is False
    assert target.internal_service_token_required is False
    assert target.features == frozenset({"chat"})
    assert target.lifecycle_owner == "external"
    assert target.validation_status == "implemented"


def test_unknown_target_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="unknown DEPLOYMENT_TARGET"):
        load_deployment_target(CATALOG, "missing-target")


def test_partial_sidecar_control_bundle_fails_closed(tmp_path) -> None:
    document = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    document["targets"]["linux-nvidia-dynamic"]["features"]["gpu_admission"] = False
    path = tmp_path / "deployment_targets.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(RuntimeError, match="enable or disable.*together"):
        load_deployment_target(path, "linux-nvidia-dynamic")


def test_non_boolean_feature_fails_closed(tmp_path) -> None:
    document = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    document["targets"]["linux-nvidia-dynamic"]["features"]["risk"] = "true"
    path = tmp_path / "deployment_targets.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(RuntimeError, match="non-boolean features: risk"):
        load_deployment_target(path, "linux-nvidia-dynamic")


def test_control_mode_and_lifecycle_owner_must_align(tmp_path) -> None:
    document = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    document["targets"]["linux-nvidia-static"]["lifecycle_owner"] = "platform"
    path = tmp_path / "deployment_targets.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(RuntimeError, match="requires lifecycle_owner='external'"):
        load_deployment_target(path, "linux-nvidia-static")


def test_planned_macos_target_cannot_be_started(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "macos-metal-static")

    with pytest.raises(RuntimeError, match="planned and cannot be started"):
        load_settings()


def test_static_settings_project_only_main_runtime(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "linux-nvidia-static")
    monkeypatch.setenv("MAIN_LLM_STATIC_PROFILE", "gemma4-e4b-it")
    monkeypatch.setenv("MAIN_LLM_BASE_URL", "http://runtime.example:9401/v1")

    settings = load_settings()

    assert settings.deployment_target.target_id == "linux-nvidia-static"
    assert set(settings.runtime_endpoints) == {"main_llm"}
    assert settings.runtime("main_llm").base_url == "http://runtime.example:9401/v1"
    assert settings.embedding_profiles == {}
    assert settings.risk_detectors == ()
    assert settings.risk_adapter_base_url == ""
    assert settings.admin_sidecar_url == ""
    assert settings.static_main_profile == "gemma4-e4b-it"
    assert settings.default_main_model_gateway_policy["max_output_tokens"] == 15_000
    assert [item["id"] for item in settings.public_models] == ["local-main"]


def test_static_gateway_surface_and_clients_are_main_only(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "linux-nvidia-static")
    monkeypatch.setenv("MAIN_LLM_STATIC_PROFILE", "gemma4-12b-unified-fp8")
    settings = load_settings()
    clients = GatewayClients(settings)
    try:
        assert clients.sidecar is None
        assert clients.embedding_clients == {}
        assert clients.risk_adapter is None
        assert set(clients.runtimes) == {"main_llm"}
    finally:
        import asyncio

        asyncio.run(clients.close())

    fake_clients = FakeGatewayClients()
    fake_clients.sidecar = None
    app = create_gateway_app(settings, fake_clients)
    client = TestClient(app)
    paths = set(app.openapi()["paths"])

    assert "/v1/chat/completions" in paths
    assert "/v1/embeddings" not in paths
    assert "/v1/retrieval/rerank" not in paths
    assert "/v1/risk/assessments" not in paths
    assert "/admin/runtimes" not in paths
    assert [item["id"] for item in client.get("/v1/models").json()["data"]] == ["local-main"]


def test_static_readiness_depends_only_on_main(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "linux-nvidia-static")
    monkeypatch.setenv("MAIN_LLM_STATIC_PROFILE", "gemma4-12b-unified-fp8")
    settings = load_settings()
    clients = FakeGatewayClients()
    clients.sidecar = None
    app = create_gateway_app(settings, clients)

    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["dependencies"]] == ["main_llm_vllm"]


def test_static_readiness_fails_when_external_main_is_down(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "linux-nvidia-static")
    monkeypatch.setenv("MAIN_LLM_STATIC_PROFILE", "gemma4-12b-unified-fp8")
    settings = load_settings()
    clients = FakeGatewayClients()
    clients.sidecar = None
    clients.main_llm = FakeRuntimeClient(
        ready=False,
        get_response={"error": "model not loaded"},
        endpoint=RuntimeEndpoint("local-main", "http://main/v1", "local-main", 1),
    )
    app = create_gateway_app(settings, clients)

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["required_not_ready_dependencies"] == ["main_llm_vllm"]


def test_static_target_requires_an_explicit_serving_profile(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "linux-nvidia-static")
    monkeypatch.delenv("MAIN_LLM_STATIC_PROFILE", raising=False)

    with pytest.raises(RuntimeError, match="MAIN_LLM_STATIC_PROFILE is required"):
        load_settings()
