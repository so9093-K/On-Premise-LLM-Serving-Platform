from __future__ import annotations

from .helpers import *  # noqa: F401,F403


class FakeMainModelSidecar:
    def __init__(self, gate: str = "open") -> None:
        self.gate = gate
        self.switch_requests: list[tuple[str, bool]] = []

    async def main_model(self):
        return {
            "public_model": "local-main",
            "active_profile": {"id": "gemma4-26b-a4b-fp8"},
            "gate": self.gate,
            "last_operation": (
                {"id": "op-1", "status": "starting"} if self.gate != "open" else None
            ),
        }

    async def main_model_profiles(self):
        return [
            {
                "id": "gemma4-26b-a4b-fp8",
                "served_model_name": "local-main",
                "active": True,
            },
            {
                "id": "gemma4-12b-unified-fp8",
                "served_model_name": "local-main",
                "active": False,
            },
        ]

    async def switch_main_model(
        self, profile, *, confirm_unverified=False, request_id=None
    ):
        self.switch_requests.append((profile, confirm_unverified, request_id))
        return {"operation_id": "op-2", "status": "pending"}

    async def rollback_main_model(self, *, request_id=None):
        return {"operation_id": "op-3", "status": "pending"}

    async def main_model_operation(self, operation_id):
        return {"id": operation_id, "status": "validating"}

    async def get_status(self):
        return {}


def test_chat_is_fail_closed_while_main_model_switches():
    clients = FakeGatewayClients()
    clients.sidecar = FakeMainModelSidecar(gate="closed")
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["error"]["code"] == "MAIN_MODEL_SWITCH_IN_PROGRESS"
    assert response.json()["error"]["operation_id"] == "op-1"
    assert clients.main_llm.last_payload is None


def test_main_model_admin_routes_proxy_only_profile_ids():
    clients = FakeGatewayClients()
    sidecar = FakeMainModelSidecar()
    clients.sidecar = sidecar
    client = TestClient(create_gateway_app(settings(), clients))

    status = client.get("/admin/main-model")
    assert status.status_code == 200
    assert status.json()["active_profile"]["id"] == "gemma4-26b-a4b-fp8"

    profiles = client.get("/admin/main-model/profiles")
    assert profiles.status_code == 200
    assert len(profiles.json()["profiles"]) == 2

    rejected = client.post(
        "/admin/main-model/switch",
        json={
            "profile": "gemma4-12b-unified-fp8",
            "confirm_unverified": True,
            "model_id": "attacker/arbitrary",
            "command": ["sh", "-c", "id"],
        },
    )
    assert rejected.status_code == 422
    assert sidecar.switch_requests == []

    switch = client.post(
        "/admin/main-model/switch",
        json={
            "profile": "gemma4-12b-unified-fp8",
            "confirm_unverified": True,
        },
    )
    assert switch.status_code == 202
    assert switch.json()["operation_id"] == "op-2"
    assert sidecar.switch_requests == [("gemma4-12b-unified-fp8", True, None)]

    operation = client.get("/admin/main-model/operations/op-2")
    assert operation.status_code == 200
    assert operation.json()["status"] == "validating"

    rollback = client.post(
        "/admin/main-model/rollback",
        json={"request_id": "rollback-1"},
    )
    assert rollback.status_code == 202
    assert rollback.json()["operation_id"] == "op-3"
