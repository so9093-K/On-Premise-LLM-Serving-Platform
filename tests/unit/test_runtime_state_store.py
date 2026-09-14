"""RuntimeStateStore의 desired-state persistence와 fail-closed 복구를 검증한다."""

from __future__ import annotations

import asyncio
import json

import pytest

from ai_model_serving.services.runtime_state import (
    RuntimeState,
    RuntimeStateRecord,
    RuntimeStateStore,
    _default_controllable_keys,
)


def test_default_controllable_keys_fails_when_runtime_topology_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_CONFIG_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="cannot load configuration"):
        _default_controllable_keys()


def test_runtime_state_store_persists_desired_state(tmp_path):
    path = tmp_path / "runtime-state.json"
    store = RuntimeStateStore(path)

    asyncio.run(store.set("embedding_ko", RuntimeState.stopped))

    reloaded = RuntimeStateStore(path)
    assert asyncio.run(reloaded.get("embedding_ko")) == RuntimeState.stopped
    assert asyncio.run(reloaded.get("embedding")) == RuntimeState.active
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["states"]["embedding_ko"]["state"] == "stopped"


def test_deploy_directive_applies_once_per_release(tmp_path):
    path = tmp_path / "runtime-state.json"
    keys = {"embedding", "risk_prompt"}

    first = RuntimeStateStore(
        path,
        controllable_keys=keys,
        deferred_keys=("risk_prompt",),
        release_id="release-1",
    )
    assert asyncio.run(first.get("risk_prompt")) == RuntimeState.stopped

    asyncio.run(
        first.set(
            "risk_prompt",
            RuntimeState.active,
            reason="operator_start_requested",
            source="runtime_control",
        )
    )
    restarted = RuntimeStateStore(
        path,
        controllable_keys=keys,
        deferred_keys=("risk_prompt",),
        release_id="release-1",
    )
    assert asyncio.run(restarted.get("risk_prompt")) == RuntimeState.active

    next_release = RuntimeStateStore(
        path,
        controllable_keys=keys,
        deferred_keys=("risk_prompt",),
        release_id="release-2",
    )
    assert asyncio.run(next_release.get("risk_prompt")) == RuntimeState.stopped
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["applied_release_id"] == "release-2"


def test_rollback_directive_reactivates_previously_running_runtime(tmp_path):
    path = tmp_path / "runtime-state.json"
    deployed = RuntimeStateStore(
        path,
        controllable_keys={"risk_prompt"},
        deferred_keys=("risk_prompt",),
        release_id="failed-release",
    )
    assert asyncio.run(deployed.get("risk_prompt")) == RuntimeState.stopped

    restored = RuntimeStateStore(
        path,
        controllable_keys={"risk_prompt"},
        activated_keys=("risk_prompt",),
        release_id="previous-release",
    )
    record = asyncio.run(restored.all_records())["risk_prompt"]
    assert record.state == RuntimeState.active
    assert record.reason == "restored_at_rollback"
    assert record.source == "deploy"


def test_runtime_state_store_reads_legacy_v1_and_ignores_unknown_keys(tmp_path):
    path = tmp_path / "runtime-state.json"
    path.write_text(
        '{"schema_version":1,"states":{"embedding":"stopped","unknown":"broken"}}',
        encoding="utf-8",
    )

    store = RuntimeStateStore(path)

    assert asyncio.run(store.get("embedding")) == RuntimeState.stopped
    assert asyncio.run(store.get("risk_prompt")) == RuntimeState.active
    assert store.recovery_quarantine_path is None


def test_runtime_state_store_quarantines_invalid_known_record_and_stops_all(tmp_path):
    path = tmp_path / "runtime-state.json"
    path.write_text(
        '{"schema_version":2,"states":{"embedding":{"state":"broken"}}}',
        encoding="utf-8",
    )

    store = RuntimeStateStore(path, controllable_keys={"embedding", "risk_prompt"})

    assert store.recovery_quarantine_path is not None
    assert store.recovery_quarantine_path.exists()
    assert "invalid record for embedding" in store.recovery_error
    records = asyncio.run(store.all_records())
    assert set(records) == {"embedding", "risk_prompt"}
    assert all(record.state == RuntimeState.stopped for record in records.values())
    assert all(record.reason == "state_recovery_required" for record in records.values())
    assert all(record.source == "recovery" for record in records.values())

    recovered = json.loads(path.read_text(encoding="utf-8"))
    assert recovered["schema_version"] == 2
    assert all(item["state"] == "stopped" for item in recovered["states"].values())


def test_runtime_state_store_quarantines_malformed_json_and_persists_safe_state(tmp_path):
    path = tmp_path / "runtime-state.json"
    path.write_text("{broken", encoding="utf-8")

    store = RuntimeStateStore(path, controllable_keys={"embedding"})

    assert store.recovery_quarantine_path is not None
    assert store.recovery_quarantine_path.read_text(encoding="utf-8") == "{broken"
    assert asyncio.run(store.get("embedding")) == RuntimeState.stopped
    assert json.loads(path.read_text(encoding="utf-8"))["states"]["embedding"]["state"] == "stopped"


def test_corruption_recovery_wins_over_deploy_activation(tmp_path):
    path = tmp_path / "runtime-state.json"
    path.write_text("{broken", encoding="utf-8")

    store = RuntimeStateStore(
        path,
        controllable_keys={"risk_prompt"},
        activated_keys=("risk_prompt",),
        release_id="release-after-corruption",
    )

    record = asyncio.run(store.all_records())["risk_prompt"]
    assert record.state == RuntimeState.stopped
    assert record.source == "recovery"
    assert record.reason == "state_recovery_required"


def test_runtime_state_store_honors_explicit_empty_controllable_keys(tmp_path):
    path = tmp_path / "runtime-state.json"
    store = RuntimeStateStore(path, controllable_keys=set())

    asyncio.run(store.set("embedding", RuntimeState.stopped))

    assert asyncio.run(store.all_records()) == {}
    assert not path.exists()


def test_runtime_state_store_reads_record_metadata(tmp_path):
    path = tmp_path / "runtime-state.json"
    path.write_text(
        json.dumps({
            "schema_version": 2,
            "states": {
                "embedding": {
                    "state": "stopped",
                    "reason": "deferred_at_deploy",
                    "source": "deploy",
                    "updated_at": 123.5,
                }
            },
        }),
        encoding="utf-8",
    )

    store = RuntimeStateStore(path)
    # all_records()가 프로덕션(gateway_runtime_control)이 실제로 쓰는 접근자다.
    record = asyncio.run(store.all_records())["embedding"]

    assert record == RuntimeStateRecord(
        RuntimeState.stopped,
        reason="deferred_at_deploy",
        source="deploy",
        updated_at=123.5,
    )


def test_runtime_state_store_tolerates_invalid_record_metadata(tmp_path):
    path = tmp_path / "runtime-state.json"
    path.write_text(
        json.dumps({
            "schema_version": 2,
            "states": {
                "embedding": {
                    "state": "stopped",
                    "reason": "manual",
                    "source": "operator",
                    "updated_at": "not-a-number",
                }
            },
        }),
        encoding="utf-8",
    )

    store = RuntimeStateStore(path)
    record = asyncio.run(store.all_records())["embedding"]

    assert record.state == RuntimeState.stopped
    assert record.reason == "manual"
    assert record.source == "operator"
    assert record.updated_at == 0.0


def test_runtime_state_store_writes_reason_metadata(tmp_path):
    path = tmp_path / "runtime-state.json"
    store = RuntimeStateStore(path)

    asyncio.run(
        store.set(
            "risk_prompt",
            RuntimeState.stopped,
            reason="operator_stop_requested",
            source="runtime_control",
        )
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    record = payload["states"]["risk_prompt"]
    assert record["state"] == "stopped"
    assert record["reason"] == "operator_stop_requested"
    assert record["source"] == "runtime_control"
    assert isinstance(record["updated_at"], float)


def test_runtime_state_store_restores_memory_when_persistence_fails(tmp_path, monkeypatch):
    path = tmp_path / "runtime-state.json"
    store = RuntimeStateStore(path, controllable_keys={"embedding"})
    before = asyncio.run(store.all_records())["embedding"]

    def fail_write() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write_file", fail_write)
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            store.set(
                "embedding",
                RuntimeState.stopped,
                reason="operator_stop_requested",
                source="runtime_control",
            )
        )

    assert asyncio.run(store.all_records())["embedding"] == before
