"""gateway가 노출하는 main-model 제어 admin 라우트(/admin/main-model/*)를
검증한다. 상태 머신 자체(전환/롤백/락)는 tests/unit/test_main_model_control.py가
sidecar 쪽에서 직접 다루고, 여기는 gateway가 그 API를 올바르게 프록시하는지만
본다(예: fail-closed 응답, profile_id만 넘기고 임의 명령은 거부)."""

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

    async def main_model_operation(self, operation_id):
        return {"id": operation_id, "status": "validating"}

    async def get_status(self):
        return {}


def test_chat_uses_active_profile_request_limit() -> None:
    """전환된 Profile의 API 한도가 정적 기본값보다 우선해야 한다."""
    class ProfilePolicySidecar(FakeMainModelSidecar):
        async def main_model(self):
            return {
                "gate": "open",
                "active_profile": {
                    "id": "small-context",
                    "capabilities": {"deployed_input": ["text"]},
                    "gateway_policy": {
                        "max_output_tokens": 8,
                        "request_limits": {"input_modalities": ["text"], "max_model_len": 32},
                        "runtime_features": {},
                        "request_parameter_policy": {
                            "allow_unlisted_parameters": False,
                            "supported_parameters": ["max_tokens"],
                        },
                    },
                },
            }

    clients = FakeGatewayClients()
    clients.sidecar = ProfilePolicySidecar()
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "max_tokens": 9,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 422
    assert "max_tokens" in response.json()["error"]["message"]


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
        },
    )
    assert switch.status_code == 202
    assert switch.json()["operation_id"] == "op-2"
    assert sidecar.switch_requests == [("gemma4-12b-unified-fp8", False, None)]

    operation = client.get("/admin/main-model/operations/op-2")
    assert operation.status_code == 200
    assert operation.json()["status"] == "validating"
