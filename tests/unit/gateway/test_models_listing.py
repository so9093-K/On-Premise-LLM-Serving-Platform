"""/v1/models 응답의 input_modalities가 main-model sidecar의 active profile을
따라 동적으로 바뀌는지(그리고 sidecar 부재/장애 시 정적 기본값으로 안전하게
폴백하는지) 검증한다."""

from __future__ import annotations

import pytest

from ai_model_serving.services.sidecar_client import SidecarUnavailableError

from .helpers import *  # noqa: F401,F403


class _FakeSidecar:
    """선택된 active-profile modality 집합을 보고하는 최소 sidecar."""

    def __init__(self, deployed_input=None, *, available: bool = True):
        self._snapshot = {
            "gate": "open",
            "active_profile": {"capabilities": {"deployed_input": list(deployed_input or [])}},
        }
        self._available = available

    async def main_model(self):
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
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    main = _main_model(client.get("/v1/models", headers=auth_headers()))
    # control plane이 없음 -> static catalog 기본값(text+image), 절대 에러 아님.
    assert main["input_modalities"] == ["text", "image"]


def test_models_listing_tracks_active_profile_modalities():
    sidecar = _FakeSidecar(["text", "image", "audio", "video"])
    client = _app_with_sidecar(sidecar)
    main = _main_model(client.get("/v1/models", headers=auth_headers()))
    # 이제 audio/video 지원 프로필이 static catalog 기본값 뒤에 숨지 않고
    # 그대로 광고된다.
    assert main["input_modalities"] == ["text", "image", "audio", "video"]


def test_models_listing_falls_back_when_sidecar_unavailable():
    client = _app_with_sidecar(_FakeSidecar(available=False))
    main = _main_model(client.get("/v1/models", headers=auth_headers()))
    assert main["input_modalities"] == ["text", "image"]


