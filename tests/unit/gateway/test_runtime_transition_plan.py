from __future__ import annotations

import asyncio

from ai_model_serving.services.runtime_state import RuntimeState
from ai_model_serving.services.runtime_controller_client import RuntimeControllerRequestError
from .helpers import FakeGatewayClients, TestClient, create_gateway_app, settings


_DIGEST = "a" * 64


class PlanningSidecar:
    def __init__(self) -> None:
        self.applied_digest: str | None = None
        self.statuses = {"embed-ko": "exited", "embed": "running"}

    async def runtime_plan(self, service: str, *, desired_state: str, force: bool = False):
        assert service == "embed-ko"
        assert desired_state == "active"
        return {
            "target_key": "embed-ko",
            "desired_state": desired_state,
            "force": force,
            "current_state": "stopped",
            "no_op": False,
            "admissible": force,
            "requires_force": not force,
            "start": ["embed-ko"],
            "stop": ["embed"],
            "prerequisites": [],
            "budget": {
                "before": {"ceiling": 0.93, "used": 0.90, "free": 0.03},
                "after": {"ceiling": 0.93, "used": 0.90, "free": 0.03},
            },
            "impact": [
                {"key": "embed", "action": "stop", "criticality": "retrieval_support_path"}
            ],
            "reason": None,
            "plan_digest": _DIGEST,
        }

    async def get_status(self):
        return dict(self.statuses)

    async def start(
        self,
        container: str,
        *,
        force: bool = False,
        plan_digest: str,
    ):
        assert container == "embed-ko"
        assert force is True
        self.applied_digest = plan_digest
        self.statuses["embed-ko"] = "running"
        self.statuses["embed"] = "exited"
        return {"started": ["embed-ko"], "evicted": ["embed"]}


class DriftRejectingSidecar(PlanningSidecar):
    async def start(
        self,
        container: str,
        *,
        force: bool = False,
        plan_digest: str,
    ):
        raise RuntimeControllerRequestError(
            409,
            {
                "code": "RUNTIME_PLAN_CHANGED",
                "message": "Runtime transition plan changed after review.",
                "expected_plan_digest": plan_digest,
                "actual_plan_digest": "b" * 64,
            },
        )


def test_runtime_plan_projects_sidecar_container_names_to_public_service_keys() -> None:
    clients = FakeGatewayClients()
    clients.runtime_controller = PlanningSidecar()
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/admin/runtimes/embedding_ko/plans",
        json={"desired_state": "active", "force": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["service_key"] == "embedding_ko"
    assert body["start"] == ["embedding_ko"]
    assert body["stop"] == ["embedding"]
    assert body["impact"] == [
        {
            "service_key": "embedding",
            "action": "stop",
            "criticality": "retrieval_support_path",
        }
    ]
    assert body["requires_force"] is True
    assert body["admissible"] is False
    assert body["plan_digest"] == _DIGEST


def test_runtime_apply_forwards_reviewed_plan_digest() -> None:
    clients = FakeGatewayClients()
    sidecar = PlanningSidecar()
    clients.runtime_controller = sidecar
    client = TestClient(create_gateway_app(settings(), clients))
    asyncio.run(clients.runtime_state.set("embedding_ko", RuntimeState.stopped))

    response = client.request(
        "PATCH",
        "/admin/runtimes/embedding_ko",
        json={"desired_state": "active", "force": True, "plan_digest": _DIGEST},
    )

    assert response.status_code == 200
    assert sidecar.applied_digest == _DIGEST
    assert response.json()["evicted"] == ["embed"]


def test_runtime_plan_and_apply_use_strict_public_validation_contract() -> None:
    clients = FakeGatewayClients()
    clients.runtime_controller = PlanningSidecar()
    client = TestClient(create_gateway_app(settings(), clients))

    bad_force = client.post(
        "/admin/runtimes/embedding_ko/plans",
        json={"desired_state": "active", "force": "false"},
    )
    assert bad_force.status_code == 422
    assert bad_force.json()["error"]["code"] == "VALIDATION_ERROR"
    assert bad_force.json()["error"]["param"] == "force"

    unknown = client.request(
        "PATCH",
        "/admin/runtimes/embedding_ko",
        json={"desired_state": "active", "unknown": True},
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["param"] == "unknown"



def test_runtime_apply_requires_reviewed_plan_digest() -> None:
    clients = FakeGatewayClients()
    clients.runtime_controller = PlanningSidecar()
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.request(
        "PATCH",
        "/admin/runtimes/embedding_ko",
        json={"desired_state": "active", "force": True},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["param"] == "plan_digest"

def test_runtime_apply_rejects_reviewed_plan_when_sidecar_snapshot_changed() -> None:
    clients = FakeGatewayClients()
    clients.runtime_controller = DriftRejectingSidecar()
    client = TestClient(create_gateway_app(settings(), clients))
    asyncio.run(clients.runtime_state.set("embedding_ko", RuntimeState.stopped))

    response = client.request(
        "PATCH",
        "/admin/runtimes/embedding_ko",
        json={"desired_state": "active", "force": True, "plan_digest": _DIGEST},
    )

    assert response.status_code == 409
    assert response.headers["X-Control-Operation-ID"].startswith("rt_")
    error = response.json()["error"]
    assert error["code"] == "CONFLICT"
    assert error["details"]["reason"] == "RUNTIME_PLAN_CHANGED"
    assert error["details"]["expected_plan_digest"] == _DIGEST
    assert error["details"]["actual_plan_digest"] == "b" * 64
