from __future__ import annotations

import asyncio
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from ai_model_serving.services.runtime_state import RuntimeState
from .helpers import FakeGatewayClients, TestClient, create_gateway_app, settings

_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_ROOT = _ROOT / "specs" / "schemas"
_DIGEST = "a" * 64


def _validate(schema_name: str, payload: object) -> None:
    schema = json.loads((_SCHEMA_ROOT / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)


class ConvergingRuntimeSidecar:
    def __init__(self) -> None:
        self.running = False

    async def get_status(self):
        return {"embed-ko": "running" if self.running else "exited"}

    async def start(self, container: str, *, force: bool = False, plan_digest: str):
        assert container == "embed-ko"
        self.running = True
        return {"started": ["embed-ko"], "evicted": []}


def test_runtime_list_matches_checked_in_response_contract():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.get("/admin/runtimes")

    assert response.status_code == 200
    _validate("runtime_list_response.schema.json", response.json())


def test_runtime_apply_matches_checked_in_response_contract():
    clients = FakeGatewayClients()
    clients.runtime_controller = ConvergingRuntimeSidecar()
    client = TestClient(create_gateway_app(settings(), clients))
    asyncio.run(clients.runtime_state.set("embedding_ko", RuntimeState.stopped))

    response = client.request(
        "PATCH",
        "/admin/runtimes/embedding_ko",
        json={"desired_state": "active", "plan_digest": _DIGEST},
    )

    assert response.status_code == 200
    _validate("runtime_transition_apply_response.schema.json", response.json())
    assert response.json()["operation_status"] == "verified"
    assert response.json()["verification"]["converged"] is True
