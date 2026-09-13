from __future__ import annotations

import pytest

from ai_model_serving.runtime_configuration import (
    RuntimeConfigurationProvider,
    RuntimeConfigurationSnapshot,
)
from tests.unit.gateway.helpers import settings


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

    provider.update(max_retrieval_documents=4)

    assert view.max_retrieval_documents == 4
    assert app_settings.max_retrieval_documents != view.max_retrieval_documents


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
