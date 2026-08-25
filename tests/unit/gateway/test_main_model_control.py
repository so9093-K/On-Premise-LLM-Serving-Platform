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
        self.observed_requested: list[bool] = []

    async def main_model(self, *, observed: bool = True):
        self.observed_requested.append(observed)
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
        async def main_model(self, *, observed: bool = True):
            return {
                "gate": "open",
                "active_profile": {
                    "id": "small-context",
                    "capabilities": {"deployed_input": ["text"]},
                    "gateway_policy": {
                        "max_output_tokens": 8,
                        "request_limits": {"input_modalities": ["text"], "max_model_len": 32},
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


def test_request_path_does_not_depend_on_docker_observation():
    """요청 경로는 control-plane ledger만 읽고 Docker 관측을 요구하지 않아야 한다.

    sidecar의 관측 경로는 요청마다 Docker API를 두 번(list + inspect) 호출한다.
    요청 경로가 그걸 요구하면 추론 하나하나가 Docker daemon에 직렬로 묶여서,
    런타임이 멀쩡해도 daemon이 흔들리면 전체 chat이 실패한다. gate·active profile은
    ledger에만 있으므로 요청 경로는 관측을 요구할 이유가 없다.
    """
    clients = FakeGatewayClients()
    sidecar = FakeMainModelSidecar()
    clients.sidecar = sidecar
    client = TestClient(create_gateway_app(settings(), clients))

    client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
    )
    client.get("/v1/models", headers=auth_headers())

    assert sidecar.observed_requested, "요청 경로가 sidecar를 조회하지 않았다"
    assert not any(sidecar.observed_requested)


def test_main_model_admin_routes_proxy_only_profile_ids():
    clients = FakeGatewayClients()
    sidecar = FakeMainModelSidecar()
    clients.sidecar = sidecar
    client = TestClient(create_gateway_app(settings(), clients))

    status = client.get("/admin/main-model")
    assert status.status_code == 200
    assert status.json()["active_profile"]["id"] == "gemma4-26b-a4b-fp8"
    # 이 라우트의 존재 이유가 "저장된 상태와 실제 관측을 함께 보여주는 것"이므로,
    # 요청 경로와 달리 여기서는 Docker 관측을 생략하면 안 된다.
    assert sidecar.observed_requested == [True]

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


def test_in_flight_count_returns_to_zero_on_every_request_outcome():
    """어떤 결말로 끝나든 in-flight 집계는 0으로 돌아와야 한다.

    sidecar는 모델 전환 전에 이 값이 0이 되기를 기다린다(docker_backend.wait_for_drain).
    한 경로라도 해제를 빠뜨리면 값이 영영 0으로 안 내려가 전환이 drain 타임아웃으로
    실패한다. 성공/업스트림 오류/요청 거부/gate 차단/streaming을 한 번씩 통과시켜
    "요청이 끝나면 해제된다"는 규칙 자체를 고정한다.
    """
    # 1) 정상 응답
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    ok = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert ok.status_code == 200
    assert asyncio.run(clients.main_model_inflight.count()) == 0

    # 2) 요청 검증 실패 (ServiceError가 핸들러 밖으로 전파되는 경로)
    rejected = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": []},
    )
    assert rejected.status_code == 422
    assert asyncio.run(clients.main_model_inflight.count()) == 0

    # 3) gate 차단 (핸들러가 조기 return)
    closed_clients = FakeGatewayClients()
    closed_clients.sidecar = FakeMainModelSidecar(gate="closed")
    closed = TestClient(create_gateway_app(settings(), closed_clients))
    blocked = closed.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert blocked.status_code == 503
    assert asyncio.run(closed_clients.main_model_inflight.count()) == 0

    # 4) streaming — 본문을 끝까지 읽은 뒤에 해제되어야 한다
    stream_clients = FakeGatewayClients()
    streaming = TestClient(create_gateway_app(settings(), stream_clients))
    streamed = streaming.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert streamed.status_code == 200
    assert streamed.text
    assert asyncio.run(stream_clients.main_model_inflight.count()) == 0
