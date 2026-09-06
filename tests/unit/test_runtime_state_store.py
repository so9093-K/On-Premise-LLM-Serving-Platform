"""RuntimeStateStore(secondary 런타임의 active/stopped 상태 영속화)를 검증한다:
알 수 없거나 깨진 값의 안전한 폴백, controllable_keys 제약, reason/source
메타데이터 기록."""

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


def test_runtime_state_store_ignores_unknown_or_invalid_persisted_values(tmp_path):
    path = tmp_path / "runtime-state.json"
    path.write_text(
        '{"schema_version":1,"states":{"embedding":"stopped","unknown":"stopped","risk_prompt":"broken"}}',
        encoding="utf-8",
    )

    store = RuntimeStateStore(path)

    assert asyncio.run(store.get("embedding")) == RuntimeState.stopped
    assert asyncio.run(store.get("risk_prompt")) == RuntimeState.active


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
    # all_records()가 프로덕션(gateway_runtime_control)이 실제로 쓰는 접근자다.
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
