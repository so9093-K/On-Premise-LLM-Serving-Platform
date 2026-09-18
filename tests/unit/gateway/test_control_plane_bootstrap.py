from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.settings import load_settings
from .helpers import FakeGatewayClients, TestClient, settings


ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads(
    (ROOT / "specs/schemas/control_plane_bootstrap_response.schema.json").read_text(encoding="utf-8")
)


def _secured_settings(auth_mode: str):
    base = settings()
    return replace(
        base,
        security=replace(
            base.security,
            auth_mode=auth_mode,
            admin_api_key_required=True,
            admin_api_keys=frozenset({"admin-key"}),
        ),
        deploy_release_id="release-2026-09-14",
    )


def test_bootstrap_is_public_even_when_admin_api_requires_bearer(monkeypatch) -> None:
    monkeypatch.setenv("ACCESS_PROFILE", "private")
    cfg = _secured_settings("private_network")
    client = TestClient(create_gateway_app(cfg, FakeGatewayClients()))

    response = client.get("/admin/control-plane/bootstrap")

    assert response.status_code == 200
    body = response.json()
    Draft202012Validator(SCHEMA).validate(body)
    assert body["bootstrap_version"] == 4
    assert body["access"]["profile"] == "private"
    assert body["access"]["admin_auth_required"] is True
    assert body["deployment"]["target"] == "linux-nvidia-dynamic"
    assert body["deployment"]["implementation_status"] == "implemented"
    assert body["deployment"]["qualification_status"] == "verified"
    assert "runtime_control" in body["deployment"]["features"]
    assert body["monitoring"] == {"available": True, "grafana_available": True}
    # private/edge의 TLS ownership은 Gateway 밖에 있을 수 있어 URL을 추측하지 않는다.
    assert body["links"]["grafana"] is None
    assert client.get("/admin/config/effective").status_code == 401


def test_local_bootstrap_projects_safe_links_and_never_serializes_secrets(monkeypatch) -> None:
    monkeypatch.setenv("ACCESS_PROFILE", "local")
    monkeypatch.setenv("GRAFANA_PORT", "9511")
    cfg = replace(settings(), deploy_release_id="release-local")
    client = TestClient(create_gateway_app(cfg, FakeGatewayClients()))

    response = client.get("/admin/control-plane/bootstrap")

    assert response.status_code == 200
    body = response.json()
    Draft202012Validator(SCHEMA).validate(body)
    assert body["platform"] == {"version": "0.1.0", "release_id": "release-local"}
    assert body["access"]["profile"] == "local"
    assert body["links"]["docs"] == "/docs"
    assert body["links"]["openapi"] == "/openapi.json"
    assert body["links"]["grafana"] == "http://testserver:9511/"
    assert body["configuration"]["revision"] == 0

    serialized = json.dumps(body, ensure_ascii=False)
    assert "internal-test-key" not in serialized
    assert "http://main/v1" not in serialized
    assert "PLATFORM_STATE_DIR" not in serialized
    assert "ADMIN_API_KEY" not in serialized


@pytest.mark.parametrize(("profile", "auth_mode"), [("private", "private_network"), ("edge", "strict")])
def test_nonlocal_managed_profiles_report_auth_but_do_not_invent_grafana_url(
    monkeypatch, profile: str, auth_mode: str
) -> None:
    monkeypatch.setenv("ACCESS_PROFILE", profile)
    cfg = _secured_settings(auth_mode)
    body = TestClient(create_gateway_app(cfg, FakeGatewayClients())).get(
        "/admin/control-plane/bootstrap"
    ).json()

    assert body["access"]["profile"] == profile
    assert body["access"]["admin_auth_required"] is True
    assert body["monitoring"]["grafana_available"] is True
    assert body["links"]["grafana"] is None


def test_missing_access_profile_is_explicit_legacy_custom(monkeypatch) -> None:
    monkeypatch.delenv("ACCESS_PROFILE", raising=False)
    monkeypatch.delenv("EXPOSURE_MODE", raising=False)
    body = TestClient(create_gateway_app(settings(), FakeGatewayClients())).get(
        "/admin/control-plane/bootstrap"
    ).json()

    assert body["access"]["profile"] == "legacy/custom"
    assert body["monitoring"]["available"] is True
    assert body["monitoring"]["grafana_available"] is False
    assert body["links"]["grafana"] is None


def test_static_target_keeps_bootstrap_but_disables_runtime_and_monitoring_capabilities(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "linux-nvidia-static")
    monkeypatch.setenv("MAIN_MODEL_STATIC_PROFILE", "gemma4-12b-unified-fp8")
    monkeypatch.setenv("ACCESS_PROFILE", "local")
    cfg = load_settings()
    clients = FakeGatewayClients()
    clients.runtime_controller = None

    response = TestClient(create_gateway_app(cfg, clients)).get(
        "/admin/control-plane/bootstrap"
    )

    assert response.status_code == 200
    body = response.json()
    Draft202012Validator(SCHEMA).validate(body)
    assert body["deployment"]["target"] == "linux-nvidia-static"
    assert body["deployment"]["implementation_status"] == "implemented"
    assert body["deployment"]["qualification_status"] == "unverified"
    assert body["deployment"]["features"] == ["chat"]
    assert body["monitoring"] == {"available": False, "grafana_available": False}
    assert body["links"]["grafana"] is None
    paths = set(create_gateway_app(cfg, clients).openapi()["paths"])
    assert "/admin/control-plane/bootstrap" in paths
    assert "/admin/runtimes" not in paths


def test_macos_static_bootstrap_uses_target_monitoring_and_compose_exposure(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_TARGET", "macos-metal-static")
    monkeypatch.setenv("MAIN_MODEL_STATIC_PROFILE", "gemma4-26b-a4b-qat-4bit-mlx")
    monkeypatch.setenv("MAIN_MODEL_BASE_URL", "http://host.docker.internal:9401/v1")
    monkeypatch.setenv("ACCESS_PROFILE", "local")
    monkeypatch.setenv("GRAFANA_PORT", "9611")
    cfg = load_settings()
    clients = FakeGatewayClients()
    clients.runtime_controller = None

    response = TestClient(create_gateway_app(cfg, clients)).get(
        "/admin/control-plane/bootstrap"
    )

    assert response.status_code == 200
    body = response.json()
    Draft202012Validator(SCHEMA).validate(body)
    assert body["deployment"]["target"] == "macos-metal-static"
    assert body["deployment"]["runtime_backend"] == "mlx-vlm"
    assert body["deployment"]["implementation_status"] == "implemented"
    assert body["deployment"]["qualification_status"] == "verified"
    assert body["deployment"]["features"] == ["chat"]
    assert body["monitoring"] == {"available": True, "grafana_available": True}
    assert body["links"]["grafana"] == "http://testserver:9611/"


def test_bootstrap_uses_startup_projection_not_request_time_environment(monkeypatch) -> None:
    monkeypatch.setenv("ACCESS_PROFILE", "local")
    monkeypatch.setenv("GRAFANA_PORT", "9511")
    app = create_gateway_app(settings(), FakeGatewayClients())

    # Bootstrap posture is a composition-time projection. Request-time environment
    # changes must not silently reinterpret the already-running Gateway.
    monkeypatch.setenv("ACCESS_PROFILE", "edge")
    monkeypatch.setenv("GRAFANA_PORT", "9999")
    body = TestClient(app).get("/admin/control-plane/bootstrap").json()

    assert body["access"]["profile"] == "local"
    assert body["links"]["grafana"] == "http://testserver:9511/"
