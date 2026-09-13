from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.configuration_plane import configuration_schema_items
from ai_model_serving.operator_configuration import (
    ConfigurationValueResolver,
    OperatorConfigurationError,
    OperatorConfigurationRevisionError,
    OperatorConfigurationState,
    OperatorConfigurationStore,
    operator_metadata_by_key,
    repository_operator_defaults,
)
from tests.support.asgi import InlineASGITestClient as TestClient
from tests.unit.gateway.helpers import FakeGatewayClients, settings


def _schema_items() -> list[dict]:
    return configuration_schema_items()


def _metadata() -> dict[str, dict]:
    return operator_metadata_by_key(_schema_items())


def _defaults() -> dict[str, object]:
    return repository_operator_defaults(_schema_items())


def test_operator_store_round_trip_and_revision_precondition(tmp_path: Path) -> None:
    store = OperatorConfigurationStore(tmp_path / "config" / "operator-overrides.yaml", _metadata())

    assert store.read() == OperatorConfigurationState(revision=0, overrides={})
    state = store.replace(
        {"streaming.max_chunks": 1234},
        expected_revision=0,
    )
    assert state.revision == 1
    assert store.read().overrides == {"streaming.max_chunks": 1234}

    with pytest.raises(OperatorConfigurationRevisionError, match="expected 0, current 1"):
        store.replace({}, expected_revision=0)


def test_operator_store_rejects_non_operator_and_invalid_values(tmp_path: Path) -> None:
    store = OperatorConfigurationStore(tmp_path / "operator-overrides.yaml", _metadata())

    with pytest.raises(OperatorConfigurationError, match="not operator-owned"):
        store.replace({"security.auth_mode": "strict"}, expected_revision=0)

    with pytest.raises(OperatorConfigurationError, match="exceeds metadata maximum 32"):
        store.replace({"operational.max_retrieval_documents": 33}, expected_revision=0)


def test_operator_store_corruption_is_fail_closed_and_can_be_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "operator-overrides.yaml"
    path.write_text("version: 1\nrevision: nope\noverrides: {}\n", encoding="utf-8")
    store = OperatorConfigurationStore(path, _metadata())

    with pytest.raises(OperatorConfigurationError, match="revision must be"):
        store.read()

    quarantined = store.quarantine_corrupt_state()
    assert quarantined is not None and quarantined.exists()
    assert not path.exists()
    assert store.read() == OperatorConfigurationState(revision=0, overrides={})


def test_resolver_preserves_explicit_precedence_and_shadowing() -> None:
    defaults = _defaults()
    state = OperatorConfigurationState(
        revision=4,
        overrides={"streaming.max_chunks": 1000},
    )
    resolver = ConfigurationValueResolver(
        repository_defaults=defaults,
        operator_state=state,
        deployment_overrides={"streaming.max_chunks": 2000},
    )

    resolved = resolver.resolve("streaming.max_chunks")
    assert resolved == {
        "default_value": defaults["streaming.max_chunks"],
        "operator_value": 1000,
        "effective_value": 2000,
        "effective_source": "deployment",
        "operator_override_shadowed": True,
    }


def test_resolver_install_requires_monotonic_revision() -> None:
    defaults = _defaults()
    resolver = ConfigurationValueResolver(
        repository_defaults=defaults,
        operator_state=OperatorConfigurationState(revision=2, overrides={}),
    )
    resolver.install_operator_state(
        OperatorConfigurationState(revision=3, overrides={"streaming.max_chunks": 1000})
    )
    assert resolver.revision == 3
    assert resolver.resolve("streaming.max_chunks")["effective_value"] == 1000

    with pytest.raises(OperatorConfigurationRevisionError, match="cannot move backwards"):
        resolver.install_operator_state(OperatorConfigurationState(revision=2, overrides={}))
    with pytest.raises(OperatorConfigurationRevisionError, match="without a new revision"):
        resolver.install_operator_state(
            OperatorConfigurationState(revision=3, overrides={"streaming.max_chunks": 2000})
        )


def test_gateway_startup_hydrates_persisted_operator_state(monkeypatch, tmp_path: Path) -> None:
    state_root = tmp_path / "platform-state"
    config_dir = state_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "operator-overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "revision": 7,
                "overrides": {
                    "operational.max_retrieval_documents": 8,
                    "streaming.max_chunks": 321,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLATFORM_STATE_DIR", str(state_root))

    app_settings = settings()
    app = create_gateway_app(app_settings, FakeGatewayClients())
    snapshot = app.state.runtime_configuration.snapshot()

    assert snapshot.revision == 7
    assert snapshot.max_retrieval_documents == 8
    assert snapshot.streaming_max_chunks == 321

    body = TestClient(app).get("/admin/config/effective").json()
    assert body["revision"] == 7
    retrieval = next(
        item for item in body["items"] if item["key"] == "operational.max_retrieval_documents"
    )
    assert retrieval["default_value"] == app_settings.max_retrieval_documents
    assert retrieval["operator_value"] == 8
    assert retrieval["effective_value"] == 8
    assert retrieval["effective_source"] == "operator"
    assert retrieval["operator_override_shadowed"] is False
