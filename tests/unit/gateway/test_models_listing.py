from __future__ import annotations

import pytest

from ai_model_serving.services.sidecar_client import SidecarUnavailableError

from .helpers import *  # noqa: F401,F403


class _FakeSidecar:
    """Minimal sidecar that reports a chosen active-profile modality set."""

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
    # No control plane -> static catalog default (text+image), never an error.
    assert main["input_modalities"] == ["text", "image"]


def test_models_listing_tracks_active_profile_modalities():
    sidecar = _FakeSidecar(["text", "image", "audio", "video"])
    client = _app_with_sidecar(sidecar)
    main = _main_model(client.get("/v1/models", headers=auth_headers()))
    # An audio/video-capable profile is now advertised, not hidden behind the
    # static catalog default.
    assert main["input_modalities"] == ["text", "image", "audio", "video"]


def test_models_listing_falls_back_when_sidecar_unavailable():
    client = _app_with_sidecar(_FakeSidecar(available=False))
    main = _main_model(client.get("/v1/models", headers=auth_headers()))
    assert main["input_modalities"] == ["text", "image"]


