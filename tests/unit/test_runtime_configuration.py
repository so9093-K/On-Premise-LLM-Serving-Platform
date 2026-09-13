from __future__ import annotations

from dataclasses import replace

import pytest

from ai_model_serving.runtime_configuration import (
    RuntimeConfigurationProvider,
    RuntimeConfigurationSettingsView,
    RuntimeConfigurationSnapshot,
)
from ai_model_serving.services.gateway_service import GatewayService
from ai_model_serving.metrics import Metrics
from tests.unit.gateway.helpers import FakeGatewayClients, settings


def test_runtime_configuration_snapshot_is_derived_from_startup_settings() -> None:
    app_settings = replace(
        settings(),
        gateway_timeout_seconds=9.5,
        max_retrieval_documents=17,
        streaming_max_duration_seconds=42.0,
        streaming_max_chunks=321,
        streaming_max_bytes=654_321,
    )

    snapshot = RuntimeConfigurationSnapshot.from_settings(app_settings)

    assert snapshot.revision == 0
    assert snapshot.gateway_timeout_seconds == 9.5
    assert snapshot.max_retrieval_documents == 17
    assert snapshot.streaming_max_duration_seconds == 42.0
    assert snapshot.streaming_max_chunks == 321
    assert snapshot.streaming_max_bytes == 654_321


def test_runtime_configuration_provider_replaces_complete_validated_snapshot() -> None:
    provider = RuntimeConfigurationProvider(
        RuntimeConfigurationSnapshot(max_retrieval_documents=32)
    )

    updated = provider.replace(max_retrieval_documents=16, streaming_max_chunks=10_000)

    assert updated.revision == 1
    assert updated.max_retrieval_documents == 16
    assert updated.streaming_max_chunks == 10_000
    assert provider.snapshot() is updated

    with pytest.raises(ValueError, match="max_retrieval_documents"):
        provider.replace(max_retrieval_documents=0)

    # A rejected update must not publish a partially-created or invalid snapshot.
    assert provider.snapshot() is updated


def test_settings_view_keeps_startup_settings_immutable_and_overlays_dynamic_policy() -> None:
    app_settings = settings()
    provider = RuntimeConfigurationProvider.from_settings(app_settings)
    view = RuntimeConfigurationSettingsView(app_settings, provider)

    original_limit = app_settings.max_retrieval_documents
    provider.replace(max_retrieval_documents=7, streaming_max_bytes=12_345)

    assert app_settings.max_retrieval_documents == original_limit
    assert view.max_retrieval_documents == 7
    assert view.streaming_max_bytes == 12_345
    assert view.runtime("main_llm") is app_settings.runtime("main_llm")
    assert view.deployment_target is app_settings.deployment_target


@pytest.mark.asyncio
async def test_gateway_service_consumes_new_runtime_policy_without_reconstruction() -> None:
    app_settings = settings()
    provider = RuntimeConfigurationProvider.from_settings(app_settings)
    view = RuntimeConfigurationSettingsView(app_settings, provider)
    clients = FakeGatewayClients()
    service = GatewayService(view, clients, Metrics("runtime-config-test"))

    documents = ["one", "two"]
    payload = {"query": "q", "documents": documents, "model": "local-embed-ko"}

    # The startup default allows the request.
    response = await service.score_documents(payload)
    assert len(response["scores"]) == 2

    # Replacing the snapshot changes the already-constructed service immediately.
    provider.replace(max_retrieval_documents=1)
    with pytest.raises(Exception) as exc_info:
        await service.score_documents(payload)
    error = exc_info.value
    assert getattr(error, "code", None) == "VALIDATION_ERROR"
    assert "cannot exceed 1 items" in str(error)


def test_runtime_configuration_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError):
        RuntimeConfigurationSnapshot(gateway_timeout_seconds=0)
    with pytest.raises(ValueError):
        RuntimeConfigurationSnapshot(streaming_max_duration_seconds=0)
    with pytest.raises(ValueError):
        RuntimeConfigurationSnapshot(streaming_max_chunks=0)
    with pytest.raises(ValueError):
        RuntimeConfigurationSnapshot(streaming_max_bytes=0)
