from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.settings import SecuritySettings
from tests.support.asgi import InlineASGITestClient as TestClient
from tests.unit.gateway.helpers import FakeGatewayClients, settings


_ADMIN_TOKEN = "admin-history-test-key"
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


def _changes(value: int) -> list[dict]:
    return [{"key": "streaming.max_chunks", "op": "set", "value": value}]


def _apply(client: TestClient, revision: int, value: int) -> dict:
    changes = _changes(value)
    plan_response = client.post(
        "/admin/config/plans",
        headers=_ADMIN_HEADERS,
        json={"base_revision": revision, "changes": changes},
    )
    assert plan_response.status_code == 200
    plan = plan_response.json()
    response = client.request(
        "PATCH",
        "/admin/config",
        headers={**_ADMIN_HEADERS, "If-Match": f'"config-{revision}"'},
        json={"plan_digest": plan["plan_digest"], "changes": changes},
    )
    assert response.status_code == 200
    return response.json()


def test_history_and_rollback_are_one_revisioned_control_plane_flow(monkeypatch, tmp_path: Path) -> None:
    app, _ = _app(monkeypatch, tmp_path)
    client = TestClient(app)
    _apply(client, 0, 1111)
    _apply(client, 1, 2222)

    history = client.get("/admin/config/history?limit=1", headers=_ADMIN_HEADERS)
    assert history.status_code == 200
    history_body = history.json()
    assert len(history_body["items"]) == 1
    assert history_body["next_cursor"] is not None
    first_item = history_body["items"][0]
    assert first_item["kind"] == "configuration_apply"
    assert first_item["candidate_revision"] == 2
    assert "overrides_before" not in first_item
    assert "overrides_after" not in first_item
    assert "error" not in first_item

    next_page = client.get(
        "/admin/config/history",
        headers=_ADMIN_HEADERS,
        params={"limit": 1, "cursor": history_body["next_cursor"]},
    )
    assert next_page.status_code == 200
    assert len(next_page.json()["items"]) == 1
    assert next_page.json()["next_cursor"] is None

    rollback_plan_response = client.post(
        "/admin/config/rollbacks/plans",
        headers=_ADMIN_HEADERS,
        json={"base_revision": 2, "target_revision": 1},
    )
    assert rollback_plan_response.status_code == 200
    rollback_plan = rollback_plan_response.json()
    assert rollback_plan["kind"] == "configuration_rollback"
    assert rollback_plan["target_revision"] == 1
    assert rollback_plan["candidate_revision"] == 3
    assert rollback_plan["changes"][0]["operator_before"] == 2222
    assert rollback_plan["changes"][0]["operator_after"] == 1111

    missing_precondition = client.post(
        "/admin/config/rollbacks",
        headers=_ADMIN_HEADERS,
        json={
            "target_revision": 1,
            "plan_digest": rollback_plan["plan_digest"],
        },
    )
    assert missing_precondition.status_code == 428
    assert missing_precondition.json()["error"]["code"] == "PRECONDITION_REQUIRED"

    rolled_back = client.post(
        "/admin/config/rollbacks",
        headers={**_ADMIN_HEADERS, "If-Match": '"config-2"'},
        json={
            "target_revision": 1,
            "plan_digest": rollback_plan["plan_digest"],
        },
    )
    assert rolled_back.status_code == 200
    assert rolled_back.headers["etag"] == '"config-3"'
    assert rolled_back.json()["revision"] == 3
    assert rolled_back.json()["target_revision"] == 1
    assert rolled_back.json()["verification"]["synchronized"] is True

    effective = client.get("/admin/config/effective", headers=_ADMIN_HEADERS)
    item = next(item for item in effective.json()["items"] if item["key"] == "streaming.max_chunks")
    assert effective.headers["etag"] == '"config-3"'
    assert item["operator_value"] == 1111

    latest = client.get("/admin/config/history?limit=1", headers=_ADMIN_HEADERS).json()["items"][0]
    assert latest["kind"] == "configuration_rollback"
    assert latest["target_revision"] == 1
    assert latest["base_revision"] == 2
    assert latest["candidate_revision"] == 3


def test_rollback_plan_rejects_stale_and_unknown_revision(monkeypatch, tmp_path: Path) -> None:
    app, _ = _app(monkeypatch, tmp_path)
    client = TestClient(app)
    _apply(client, 0, 1234)

    current = client.post(
        "/admin/config/rollbacks/plans",
        headers=_ADMIN_HEADERS,
        json={"base_revision": 1, "target_revision": 1},
    )
    assert current.status_code == 412
    assert current.json()["error"]["details"]["reason"] == "rollback_already_current"

    unknown = client.post(
        "/admin/config/rollbacks/plans",
        headers=_ADMIN_HEADERS,
        json={"base_revision": 1, "target_revision": 99},
    )
    assert unknown.status_code == 412
    assert unknown.json()["error"]["details"]["reason"] == "history_revision_not_found"

    stale = client.post(
        "/admin/config/rollbacks/plans",
        headers=_ADMIN_HEADERS,
        json={"base_revision": 0, "target_revision": 0},
    )
    assert stale.status_code == 412
    assert stale.json()["error"]["details"]["current_revision"] == 1


def test_history_query_uses_standard_validation_contract(monkeypatch, tmp_path: Path) -> None:
    app, _ = _app(monkeypatch, tmp_path)
    client = TestClient(app)

    invalid_limit = client.get(
        "/admin/config/history?limit=zero",
        headers=_ADMIN_HEADERS,
    )
    assert invalid_limit.status_code == 422
    error = invalid_limit.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["param"] == "limit"

    duplicate = client.get(
        "/admin/config/history?limit=1&limit=2",
        headers=_ADMIN_HEADERS,
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["code"] == "VALIDATION_ERROR"
    assert duplicate.json()["error"]["param"] == "limit"

    unknown = client.get(
        "/admin/config/history?unexpected=1",
        headers=_ADMIN_HEADERS,
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "VALIDATION_ERROR"
    assert unknown.json()["error"]["param"] == "query"


def test_history_failure_does_not_expose_internal_path(monkeypatch, tmp_path: Path) -> None:
    app, state_root = _app(monkeypatch, tmp_path)
    history_dir = state_root / "config" / "history"
    corrupt = history_dir / f"cfg_{'a' * 32}.json"
    corrupt.write_text("{not-json", encoding="utf-8")

    response = TestClient(app).get("/admin/config/history", headers=_ADMIN_HEADERS)
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "CONFIGURATION_WRITE_UNAVAILABLE"
    assert error["details"]["reason"] == "history_unreadable"
    assert str(state_root) not in response.text


def test_history_and_rollback_contracts_are_in_generated_openapi(monkeypatch, tmp_path: Path) -> None:
    app, _ = _app(monkeypatch, tmp_path)
    paths = app.openapi()["paths"]

    assert paths["/admin/config/history"]["get"]["x-response-contract-schema"] == (
        "configuration_history_response.schema.json"
    )
    assert paths["/admin/config/rollbacks/plans"]["post"]["x-contract-schema"] == (
        "configuration_rollback_plan_request.schema.json"
    )
    assert paths["/admin/config/rollbacks/plans"]["post"]["x-response-contract-schema"] == (
        "configuration_rollback_plan_response.schema.json"
    )
    assert paths["/admin/config/rollbacks"]["post"]["x-contract-schema"] == (
        "configuration_rollback_apply_request.schema.json"
    )
    assert paths["/admin/config/rollbacks"]["post"]["x-response-contract-schema"] == (
        "configuration_rollback_apply_response.schema.json"
    )
    parameters = paths["/admin/config/rollbacks"]["post"]["parameters"]
    assert any(parameter["name"] == "If-Match" and parameter["required"] for parameter in parameters)
