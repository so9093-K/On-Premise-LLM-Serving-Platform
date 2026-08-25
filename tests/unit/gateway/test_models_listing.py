"""/v1/models 응답의 input_modalities가 main-model sidecar의 active profile을
따라 동적으로 바뀌는지(그리고 sidecar 부재/장애 시 정적 기본값으로 안전하게
폴백하는지) 검증한다."""

from __future__ import annotations

import pytest

from ai_model_serving.services.sidecar_client import SidecarUnavailableError

from .helpers import *  # noqa: F401,F403


class _FakeSidecar:
    """선택된 active-profile modality 집합을 보고하는 최소 sidecar."""

    def __init__(self, deployed_input=None, *, gateway_policy=None, available: bool = True):
        self._snapshot = {
            "gate": "open",
            "active_profile": {
                "capabilities": {"deployed_input": list(deployed_input or [])},
                "gateway_policy": gateway_policy,
            },
        }
        self._available = available
        self.observed_requested: list[bool] = []

    async def main_model(self, *, observed: bool = True):
        self.observed_requested.append(observed)
        if not self._available:
            raise SidecarUnavailableError("sidecar down")
        return self._snapshot


def _app_with_sidecar(sidecar):
    clients = FakeGatewayClients()
    clients.sidecar = sidecar
    return TestClient(create_gateway_app(settings(), clients))


def _main_model(response):
    return next(m for m in response.json()["data"] if m["id"] == "local-main")


def test_models_listing_exposes_static_input_modalities_without_sidecar():
    cfg = settings()
    client = TestClient(create_gateway_app(cfg, FakeGatewayClients()))
    main = _main_model(client.get("/v1/models", headers=auth_headers()))
    # control plane이 없음 -> static catalog 기본값(text+image), 절대 에러 아님.
    assert main["input_modalities"] == list(cfg.runtime("main_llm").allowed_input_modalities)


def test_models_listing_tracks_active_profile_modalities():
    sidecar = _FakeSidecar(["text", "image", "audio", "video"])
    client = _app_with_sidecar(sidecar)
    main = _main_model(client.get("/v1/models", headers=auth_headers()))
    # 이제 audio/video 지원 프로필이 static catalog 기본값 뒤에 숨지 않고
    # 그대로 광고된다.
    assert main["input_modalities"] == ["text", "image", "audio", "video"]


def test_models_listing_does_not_advertise_tools_when_active_profile_rejects_them():
    sidecar = _FakeSidecar(
        ["text", "image", "audio", "video"],
        gateway_policy={"request_parameter_policy": {"supported_parameters": ["max_tokens"]}},
    )
    main = _main_model(_app_with_sidecar(sidecar).get("/v1/models", headers=auth_headers()))
    assert "chat.completions.tools" not in main["capabilities"]


def test_models_listing_falls_back_when_sidecar_unavailable():
    cfg = settings()
    clients = FakeGatewayClients()
    clients.sidecar = _FakeSidecar(available=False)
    client = TestClient(create_gateway_app(cfg, clients))
    main = _main_model(client.get("/v1/models", headers=auth_headers()))
    assert main["input_modalities"] == list(cfg.runtime("main_llm").allowed_input_modalities)


def test_models_listing_carries_openai_model_object_fields():
    """OpenAI model object가 요구하는 created·owned_by를 함께 실어야 한다.

    이 두 필드가 없으면 표준 클라이언트가 `/v1/models` 항목을 model object로
    다루지 못한다(업스트림 vLLM은 둘 다 반환하는데 Gateway만 빠뜨리고 있었다).
    created는 프로세스 기동 시각이라 같은 응답 안에서, 그리고 호출 사이에서
    흔들리지 않아야 한다 -- 매번 달라지면 listing을 캐시하는 쪽이 모델이 새로
    생긴 것으로 읽는다.
    """
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    first = client.get("/v1/models", headers=auth_headers()).json()["data"]
    second = client.get("/v1/models", headers=auth_headers()).json()["data"]

    assert first, "모델 목록이 비어 있다"
    for item in first:
        assert item["object"] == "model"
        assert isinstance(item["created"], int) and item["created"] > 0
        assert isinstance(item["owned_by"], str) and item["owned_by"]
    assert len({item["created"] for item in first}) == 1
    assert [item["created"] for item in first] == [item["created"] for item in second]
