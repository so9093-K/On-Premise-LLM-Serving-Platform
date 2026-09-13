from __future__ import annotations

import pytest

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.errors import ServiceError
from ai_model_serving.metrics import Metrics
from ai_model_serving.runtime_configuration import (
    RuntimeConfigurationProvider,
    RuntimeConfigurationSnapshot,
)
from ai_model_serving.services.retrieval_service import RetrievalService
from tests.support.asgi import InlineASGITestClient as TestClient
from tests.unit.gateway.helpers import FakeGatewayClients, auth_headers, settings


def test_runtime_configuration_starts_from_resolved_settings() -> None:
    app_settings = settings()
    provider = RuntimeConfigurationProvider.from_settings(app_settings)

    snapshot = provider.snapshot()

    assert snapshot.revision == 0
    assert snapshot.max_retrieval_documents == app_settings.max_retrieval_documents
    assert snapshot.streaming_max_duration_seconds == app_settings.streaming_max_duration_seconds
    assert snapshot.streaming_max_chunks == app_settings.streaming_max_chunks
    assert snapshot.streaming_max_bytes == app_settings.streaming_max_bytes


def test_runtime_configuration_update_replaces_snapshot_atomically() -> None:
    provider = RuntimeConfigurationProvider.from_settings(settings())
    before = provider.snapshot()

    after = provider.update(
        max_retrieval_documents=8,
        streaming_max_chunks=1234,
    )

    assert before.revision == 0
    assert before.max_retrieval_documents != after.max_retrieval_documents
    assert after.revision == 1
    assert after.max_retrieval_documents == 8
    assert after.streaming_max_chunks == 1234
    assert provider.snapshot() is after


def test_runtime_configuration_settings_view_reads_mutable_policy_only() -> None:
    app_settings = settings()
    provider = RuntimeConfigurationProvider.from_settings(app_settings)
    view = provider.settings_view(app_settings)

    assert view.project_version == app_settings.project_version
    assert view.runtime("main_llm") is app_settings.runtime("main_llm")
    assert view.max_retrieval_documents == app_settings.max_retrieval_documents
    assert RuntimeConfigurationProvider.from_settings(view) is provider

    provider.update(max_retrieval_documents=4)

    assert view.max_retrieval_documents == 4
    assert app_settings.max_retrieval_documents != view.max_retrieval_documents


def test_retrieval_service_reads_new_snapshot_on_the_next_request() -> None:
    app_settings = settings()
    provider = RuntimeConfigurationProvider.from_settings(app_settings)
    service = RetrievalService(
        app_settings,
        FakeGatewayClients(),
        Metrics("runtime_config_test"),
        runtime_configuration=provider,
    )
    payload = {
        "model": "local-embed",
        "query": "q",
        "documents": ["one", "two"],
    }

    service._validate_query_documents_payload(payload, operation="score")
    provider.update(max_retrieval_documents=1)

    with pytest.raises(ServiceError, match="cannot exceed 1 items"):
        service._validate_query_documents_payload(payload, operation="score")

    assert app_settings.max_retrieval_documents != 1


def test_gateway_uses_one_runtime_provider_for_retrieval_and_streaming() -> None:
    app_settings = settings()
    clients = FakeGatewayClients()
    app = create_gateway_app(app_settings, clients)
    client = TestClient(app)
    provider = app.state.runtime_configuration

    retrieval_payload = {
        "model": "local-embed",
        "query": "q",
        "documents": ["one", "two"],
    }
    before = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json=retrieval_payload,
    )
    assert before.status_code == 200

    clients.main_llm.stream_chunks = [
        b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"second"}}]}\n\n',
        b'data: [DONE]\n\n',
    ]
    provider.update(max_retrieval_documents=1, streaming_max_chunks=1)

    retrieval_after = client.post(
        "/v1/retrieval/score",
        headers=auth_headers(),
        json=retrieval_payload,
    )
    assert retrieval_after.status_code == 422
    assert "cannot exceed 1 items" in retrieval_after.json()["error"]["message"]

    streaming_after = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert streaming_after.status_code == 200
    assert "STREAM_LIMIT_EXCEEDED" in streaming_after.content.decode()
    assert app_settings.max_retrieval_documents != 1
    assert app_settings.streaming_max_chunks != 1


def test_runtime_configuration_rejects_invalid_or_unknown_changes() -> None:
    provider = RuntimeConfigurationProvider.from_settings(settings())

    with pytest.raises(ValueError, match="max_retrieval_documents"):
        provider.update(max_retrieval_documents=0)
    with pytest.raises(ValueError, match="unknown runtime configuration fields"):
        provider.update(admin_sidecar_url="http://example.invalid")

    assert provider.snapshot().revision == 0


def test_runtime_configuration_install_never_moves_revision_backwards() -> None:
    provider = RuntimeConfigurationProvider.from_settings(settings())
    provider.update(max_retrieval_documents=8)

    with pytest.raises(ValueError, match="cannot move backwards"):
        provider.install(
            RuntimeConfigurationSnapshot(
                revision=0,
                max_retrieval_documents=8,
                streaming_max_duration_seconds=300,
                streaming_max_chunks=20_000,
                streaming_max_bytes=104_857_600,
            )
        )


def test_runtime_configuration_same_revision_cannot_change_values() -> None:
    provider = RuntimeConfigurationProvider.from_settings(settings())
    current = provider.snapshot()

    assert provider.install(current) is current

    with pytest.raises(ValueError, match="cannot change without a new revision"):
        provider.install(
            RuntimeConfigurationSnapshot(
                revision=current.revision,
                max_retrieval_documents=current.max_retrieval_documents + 1,
                streaming_max_duration_seconds=current.streaming_max_duration_seconds,
                streaming_max_chunks=current.streaming_max_chunks,
                streaming_max_bytes=current.streaming_max_bytes,
            )
        )
