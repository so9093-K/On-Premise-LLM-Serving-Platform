"""SidecarClient가 sidecar 응답을 호출자 계약으로 옮기는 규칙을 검증한다.

이 client는 gateway가 sidecar에 접근하는 유일한 통로이고, chat 요청 경로에도
들어있다. 특히 두 가지가 섞이면 안 된다 -- "sidecar에 닿지 못했다"(Unavailable)와
"sidecar가 요청을 거부했다"(RequestError, 예: GPU 예산 부족 409)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ai_model_serving.services.sidecar_client import (
    SidecarClient,
    SidecarRequestError,
    SidecarUnavailableError,
)


def _client(handler) -> SidecarClient:
    client = SidecarClient("http://sidecar:8080", "token")
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer token"}
    )
    return client


def test_main_model_ledger_only_request_skips_docker_observation():
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"gate": "open"})

    client = _client(handler)
    asyncio.run(client.main_model())
    asyncio.run(client.main_model(observed=False))

    assert "observed" not in seen[0].params
    assert seen[1].params["observed"] == "false"


def test_budget_rejection_keeps_status_and_plan_but_other_errors_do_not():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/start"):
            return httpx.Response(409, json={"detail": {"evict": ["embedding"]}})
        return httpx.Response(500, json={"detail": "boom"})

    client = _client(handler)

    with pytest.raises(SidecarRequestError) as budget:
        asyncio.run(client.start("main-llm-vllm"))
    assert budget.value.status_code == 409
    assert budget.value.detail == {"evict": ["embedding"]}

    # 500은 계획을 담은 거부가 아니라 그냥 control-plane 장애다. 다만 sidecar에
    # 닿지도 못한 경우와 구분되도록 상태 코드는 메시지에 남아야 한다.
    with pytest.raises(SidecarUnavailableError) as failure:
        asyncio.run(client.gpu_budget())
    assert "500" in str(failure.value)


def test_transport_failure_is_reported_as_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(SidecarUnavailableError):
        asyncio.run(_client(handler).main_model(observed=False))
