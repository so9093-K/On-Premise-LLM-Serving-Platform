"""통합 /admin/runtimes budget aggregation 헬퍼에 대한 단위 테스트."""

from __future__ import annotations

import asyncio

from ai_model_serving.api.routers.gateway_runtime_control import (
    _sidecar_request_error_response,
)
from ai_model_serving.services.sidecar_client import SidecarRequestError
from ai_model_serving.services.runtime_state import RuntimeState
from .helpers import FakeGatewayClients, TestClient, create_gateway_app, settings

class EvictingSidecar:
    async def get_status(self):
        return {"embed-ko": "exited"}

    async def start(self, container: str, *, force: bool = False):
        assert container == "embed-ko"
        assert force is True
        return {"started": ["embed-ko"], "evicted": ["embed"]}


class StartReconcilingSidecar:
    def __init__(self):
        self.started = False

    async def get_status(self):
        return {"embed-ko": "exited"}

    async def start(self, container: str, *, force: bool = False):
        assert container == "embed-ko"
        self.started = True
        return {"started": ["embed-ko"], "evicted": []}


class StopReconcilingSidecar:
    def __init__(self):
        self.stopped = False

    async def get_status(self):
        return {"embed-ko": "running"}

    async def stop(self, container: str):
        assert container == "embed-ko"
        self.stopped = True
        return ["embed-ko"]


class MainStartEvictingSidecar:
    async def main_start(self, *, force: bool = False):
        assert force is True
        return {"runtime_state": "active", "evicted": ["embed-ko"]}


class BudgetRejectingSidecar:
    async def get_status(self):
        return {"embed-ko": "exited"}

    async def start(self, container: str, *, force: bool = False):
        raise SidecarRequestError(
            409,
            {
                "code": "GPU_BUDGET_EXCEEDED",
                "message": "GPU budget does not allow this activation.",
                "feasible": True,
                "plan": {"stop": ["risk-prompt-vllm"]},
            },
        )


def test_runtime_start_marks_sidecar_evictions_stopped():
    clients = FakeGatewayClients()
    clients.sidecar = EvictingSidecar()
    client = TestClient(create_gateway_app(settings(), clients))

    asyncio.run(clients.runtime_state.set("embedding_ko", RuntimeState.stopped))
    response = client.request(
        "PATCH",
        "/admin/runtimes/embedding_ko",
        json={"desired_state": "active", "force": True},
    )

    assert response.status_code == 200
    assert response.json()["containers_started"] == ["embed-ko"]
    assert response.json()["evicted"] == ["embed"]
    assert asyncio.run(clients.runtime_state.get("embedding")) == RuntimeState.stopped
    assert asyncio.run(clients.runtime_state.get("embedding_ko")) == RuntimeState.active


def test_runtime_active_reconciles_when_desired_active_but_container_is_down():
    clients = FakeGatewayClients()
    sidecar = StartReconcilingSidecar()
    clients.sidecar = sidecar
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.request(
        "PATCH",
        "/admin/runtimes/embedding_ko",
        json={"desired_state": "active"},
    )

    assert response.status_code == 200
    assert sidecar.started is True
    assert response.json()["containers_started"] == ["embed-ko"]


def test_runtime_budget_rejection_uses_standard_error_envelope():
    clients = FakeGatewayClients()
    clients.sidecar = BudgetRejectingSidecar()
    client = TestClient(create_gateway_app(settings(), clients))

    asyncio.run(clients.runtime_state.set("embedding_ko", RuntimeState.stopped))
    response = client.request(
        "PATCH",
        "/admin/runtimes/embedding_ko",
        json={"desired_state": "active"},
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "GPU_BUDGET_EXCEEDED"
    assert error["details"] == {
        "feasible": True,
        "plan": {"stop": ["risk-prompt-vllm"]},
    }


def test_sidecar_non_conflict_error_uses_its_http_status_code():
    response = _sidecar_request_error_response(
        SidecarRequestError(
            422,
            {"code": "MODEL_PROFILE_INCOMPATIBLE", "message": "profile is incompatible"},
        )
    )

    assert response.status_code == 422
    assert b'"code":"VALIDATION_ERROR"' in response.body
    assert b'"reason":"MODEL_PROFILE_INCOMPATIBLE"' in response.body


def test_runtime_stop_reconciles_when_desired_stopped_but_container_is_running():
    clients = FakeGatewayClients()
    sidecar = StopReconcilingSidecar()
    clients.sidecar = sidecar
    client = TestClient(create_gateway_app(settings(), clients))

    asyncio.run(clients.runtime_state.set("embedding_ko", RuntimeState.stopped))
    response = client.request(
        "PATCH",
        "/admin/runtimes/embedding_ko",
        json={"desired_state": "stopped"},
    )

    assert response.status_code == 200
    assert sidecar.stopped is True
    assert response.json()["containers_stopped"] == ["embed-ko"]


def test_main_runtime_start_marks_sidecar_evictions_stopped():
    clients = FakeGatewayClients()
    clients.sidecar = MainStartEvictingSidecar()
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.request(
        "PATCH",
        "/admin/runtimes/main",
        json={"desired_state": "active", "force": True},
    )

    assert response.status_code == 200
    assert response.json()["evicted"] == ["embed-ko"]
    assert asyncio.run(clients.runtime_state.get("embedding_ko")) == RuntimeState.stopped
