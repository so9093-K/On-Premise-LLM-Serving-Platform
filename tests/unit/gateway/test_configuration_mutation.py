from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.settings import SecuritySettings
from tests.support.asgi import InlineASGITestClient as TestClient
from tests.unit.gateway.helpers import FakeGatewayClients, settings


_ADMIN_TOKEN = "admin-config-test-key"
_ADMIN_HEADERS = {"Authorization": f"Bearer {_ADMIN_TOKEN}"}


def _app(monkeypatch, tmp_path: Path):
    state_root = tmp_path / "platform-state"
    monkeypatch.setenv("PLATFORM_STATE_DIR", str(state_root))
    original = settings()
    app_settings = replace(
        original,
        security=SecuritySettings(
            api_key_required=True,
            api_keys=frozenset({"test-key"}),
            internal_service_token="internal-test-key",
            admin_api_key_required=True,
            admin_api_keys=frozenset({_ADMIN_TOKEN}),
        ),
    )
    return create_gateway_app(app_settings, FakeGatewayClients()), state_root


def _changes(value: int = 1234) -> list[dict]:
    return [{"key": "streaming.max_chunks", "op": "set", "value": value}]


def test_plan_apply_updates_persistent_and_runtime_state(monkeypatch, tmp_path: Path) -> None:
    app, state_root = _app(monkeypatch, tmp_path)
    client = TestClient(app)

    effective = client.get("/admin/config/effective", headers=_ADMIN_HEADERS)
    assert effective.status_code == 200
    assert effective.headers["etag"] == '"config-0"'

    changes = _changes()
    planned = client.post(
        "/admin/config/plans",
        headers=_ADMIN_HEADERS,
        json={"base_revision": 0, "changes": changes},
    )
    assert planned.status_code == 200
    plan = planned.json()
    assert plan["candidate_revision"] == 1
    assert plan["changes"][0]["effective_after"] == 1234

    missing_precondition = client.request(
        "PATCH",
        "/admin/config",
        headers=_ADMIN_HEADERS,
        json={"plan_digest": plan["plan_digest"], "changes": changes},
    )
    assert missing_precondition.status_code == 428
    assert missing_precondition.json()["error"]["code"] == "PRECONDITION_REQUIRED"

    applied = client.request(
        "PATCH",
        "/admin/config",
        headers={**_ADMIN_HEADERS, "If-Match": '"config-0"'},
        json={"plan_digest": plan["plan_digest"], "changes": changes},
    )
    assert applied.status_code == 200
    assert applied.headers["etag"] == '"config-1"'
    assert applied.json()["verification"]["synchronized"] is True
    assert app.state.runtime_configuration.snapshot().streaming_max_chunks == 1234

    after = client.get("/admin/config/effective", headers=_ADMIN_HEADERS)
    item = next(item for item in after.json()["items"] if item["key"] == "streaming.max_chunks")
    assert after.headers["etag"] == '"config-1"'
    assert item["operator_value"] == 1234
    assert item["effective_value"] == 1234
    assert item["effective_source"] == "operator"

    persisted = (state_root / "config" / "operator-overrides.yaml").read_text(encoding="utf-8")
    assert "streaming.max_chunks: 1234" in persisted

    records = list((state_root / "config" / "history").glob("cfg_*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["status"] == "verified"
    assert record["actor"] == {
        "auth_method": "api_key",
        "actor_id": f"admin-key:{sha256(_ADMIN_TOKEN.encode('utf-8')).hexdigest()[:16]}",
    }
    assert _ADMIN_TOKEN not in records[0].read_text(encoding="utf-8")


def test_apply_rejects_stale_revision_and_review_drift(monkeypatch, tmp_path: Path) -> None:
    app, _ = _app(monkeypatch, tmp_path)
    client = TestClient(app)
    changes = _changes(2000)
    plan = client.post(
        "/admin/config/plans",
        headers=_ADMIN_HEADERS,
        json={"base_revision": 0, "changes": changes},
    ).json()

    digest_mismatch = client.request(
        "PATCH",
        "/admin/config",
        headers={**_ADMIN_HEADERS, "If-Match": '"config-0"'},
        json={"plan_digest": "0" * 64, "changes": changes},
    )
    assert digest_mismatch.status_code == 412
    assert digest_mismatch.json()["error"]["code"] == "CONFIG_REVISION_CONFLICT"
    assert digest_mismatch.json()["error"]["details"]["reason"] == "plan_digest_mismatch"

    applied = client.request(
        "PATCH",
        "/admin/config",
        headers={**_ADMIN_HEADERS, "If-Match": '"config-0"'},
        json={"plan_digest": plan["plan_digest"], "changes": changes},
    )
    assert applied.status_code == 200

    stale = client.request(
        "PATCH",
        "/admin/config",
        headers={**_ADMIN_HEADERS, "If-Match": '"config-0"'},
        json={"plan_digest": plan["plan_digest"], "changes": changes},
    )
    assert stale.status_code == 412
    error = stale.json()["error"]
    assert error["code"] == "CONFIG_REVISION_CONFLICT"
    assert error["details"]["current_revision"] == 1


def test_reset_removes_operator_layer_instead_of_copying_default(monkeypatch, tmp_path: Path) -> None:
    app, state_root = _app(monkeypatch, tmp_path)
    client = TestClient(app)

    set_changes = _changes(3333)
    set_plan = client.post(
        "/admin/config/plans",
        headers=_ADMIN_HEADERS,
        json={"base_revision": 0, "changes": set_changes},
    ).json()
    assert client.request(
        "PATCH",
        "/admin/config",
        headers={**_ADMIN_HEADERS, "If-Match": '"config-0"'},
        json={"plan_digest": set_plan["plan_digest"], "changes": set_changes},
    ).status_code == 200

    reset_changes = [{"key": "streaming.max_chunks", "op": "reset"}]
    reset_plan = client.post(
        "/admin/config/plans",
        headers=_ADMIN_HEADERS,
        json={"base_revision": 1, "changes": reset_changes},
    ).json()
    assert reset_plan["changes"][0]["operator_after"] is None
    assert reset_plan["changes"][0]["effective_source_after"] == "repository"

    reset = client.request(
        "PATCH",
        "/admin/config",
        headers={**_ADMIN_HEADERS, "If-Match": '"config-1"'},
        json={"plan_digest": reset_plan["plan_digest"], "changes": reset_changes},
    )
    assert reset.status_code == 200
    assert reset.headers["etag"] == '"config-2"'

    state_text = (state_root / "config" / "operator-overrides.yaml").read_text(encoding="utf-8")
    assert "streaming.max_chunks" not in state_text


def test_admin_auth_failure_advertises_bearer_challenge(monkeypatch, tmp_path: Path) -> None:
    app, _ = _app(monkeypatch, tmp_path)
    response = TestClient(app).post(
        "/admin/config/plans",
        json={"base_revision": 0, "changes": _changes()},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_malformed_json_is_a_validation_error_not_internal_error(monkeypatch, tmp_path: Path) -> None:
    app, _ = _app(monkeypatch, tmp_path)
    response = TestClient(app).post(
        "/admin/config/plans",
        headers={**_ADMIN_HEADERS, "Content-Type": "application/json"},
        content=b'{"base_revision": 0, "changes":',
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["param"] == "body"
    assert error["message"] == "Request body must be valid JSON"


def test_write_unavailable_does_not_expose_internal_state_path(monkeypatch, tmp_path: Path) -> None:
    app, state_root = _app(monkeypatch, tmp_path)
    history_dir = state_root / "config" / "history"
    corrupt = history_dir / f"cfg_{'a' * 32}.json"
    corrupt.write_text("{not-json", encoding="utf-8")

    response = TestClient(app).post(
        "/admin/config/plans",
        headers=_ADMIN_HEADERS,
        json={"base_revision": 0, "changes": _changes()},
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "CONFIGURATION_WRITE_UNAVAILABLE"
    assert error["message"] == "Configuration write plane is temporarily unavailable."
    assert error["details"]["reason"] == "history_unreadable"
    assert str(state_root) not in response.text


def test_apply_openapi_declares_required_if_match_header() -> None:
    app = create_gateway_app(settings(), FakeGatewayClients())
    operation = app.openapi()["paths"]["/admin/config"]["patch"]
    parameters = operation.get("parameters", [])
    if_match = next(parameter for parameter in parameters if parameter["name"] == "If-Match")

    assert if_match["in"] == "header"
    assert if_match["required"] is True
    assert if_match["schema"]["pattern"] == '^"config-[0-9]+"$'
