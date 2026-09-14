from __future__ import annotations

from pathlib import Path

import pytest

from ai_model_serving.configuration_mutation import (
    ConfigurationHistoryStore,
    ConfigurationMutationEngine,
    ConfigurationRevisionConflict,
    ConfigurationValidationError,
    ConfigurationWriteUnavailable,
)
from ai_model_serving.configuration_plane import configuration_schema_items
from ai_model_serving.operator_configuration import (
    ConfigurationValueResolver,
    OperatorConfigurationStore,
    operator_metadata_by_key,
    repository_operator_defaults,
    runtime_snapshot_from_resolver,
)
from ai_model_serving.runtime_configuration import RuntimeConfigurationProvider


_ACTOR = {"auth_method": "local", "actor_id": "local_operator"}


def _components(tmp_path: Path):
    schema_items = configuration_schema_items()
    store = OperatorConfigurationStore(
        tmp_path / "config" / "operator-overrides.yaml",
        operator_metadata_by_key(schema_items),
    )
    state = store.read()
    resolver = ConfigurationValueResolver(
        repository_defaults=repository_operator_defaults(schema_items),
        operator_state=state,
    )
    runtime = RuntimeConfigurationProvider(runtime_snapshot_from_resolver(resolver, schema_items))
    history = ConfigurationHistoryStore(tmp_path / "config" / "history")
    engine = ConfigurationMutationEngine(
        schema_items=schema_items,
        store=store,
        resolver=resolver,
        runtime_configuration=runtime,
        history=history,
    )
    return store, resolver, runtime, history, engine


def _change(value: int) -> list[dict]:
    return [{"key": "streaming.max_chunks", "op": "set", "value": value}]


def _apply(engine: ConfigurationMutationEngine, revision: int, value: int) -> dict:
    changes = _change(value)
    plan = engine.plan(base_revision=revision, changes=changes)
    return engine.apply(
        expected_revision=revision,
        changes=changes,
        plan_digest=plan["plan_digest"],
        actor=_ACTOR,
        request_id=f"req_apply_{revision}_{value}",
    )


def test_history_projection_is_paginated_and_hides_internal_snapshots(tmp_path: Path) -> None:
    _, _, _, _, engine = _components(tmp_path)
    _apply(engine, 0, 1001)
    _apply(engine, 1, 1002)
    _apply(engine, 2, 1003)

    first = engine.history_page(limit=2)
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None
    for item in first["items"]:
        assert "overrides_before" not in item
        assert "overrides_after" not in item
        assert "error" not in item
        assert item["verification"]["synchronized"] is True

    second = engine.history_page(limit=2, cursor=first["next_cursor"])
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    operation_ids = {
        *(item["operation_id"] for item in first["items"]),
        *(item["operation_id"] for item in second["items"]),
    }
    assert len(operation_ids) == 3

    with pytest.raises(ConfigurationValidationError) as invalid:
        engine.history_page(cursor="not-a-history-cursor")
    assert invalid.value.param == "cursor"


def test_rollback_reuses_plan_apply_verify_and_creates_new_revision(tmp_path: Path) -> None:
    store, _, runtime, history, engine = _components(tmp_path)
    _apply(engine, 0, 1111)
    _apply(engine, 1, 2222)

    rollback_plan = engine.rollback_plan(base_revision=2, target_revision=1)
    assert rollback_plan["kind"] == "configuration_rollback"
    assert rollback_plan["target_revision"] == 1
    assert rollback_plan["base_revision"] == 2
    assert rollback_plan["candidate_revision"] == 3
    assert rollback_plan["would_change"] is True
    assert rollback_plan["changes"][0]["operator_before"] == 2222
    assert rollback_plan["changes"][0]["operator_after"] == 1111

    generic_plan = engine.plan(base_revision=2, changes=_change(1111))
    assert rollback_plan["plan_digest"] != generic_plan["plan_digest"]

    result = engine.rollback(
        expected_revision=2,
        target_revision=1,
        plan_digest=rollback_plan["plan_digest"],
        actor=_ACTOR,
        request_id="req_rollback_to_1",
    )
    assert result["status"] == "verified"
    assert result["changed"] is True
    assert result["revision"] == 3
    assert result["target_revision"] == 1
    assert result["verification"]["synchronized"] is True
    assert store.read().revision == 3
    assert store.read().overrides == {"streaming.max_chunks": 1111}
    assert runtime.snapshot().revision == 3
    assert runtime.snapshot().streaming_max_chunks == 1111

    rollback_records = [
        record for record in history.records() if record.get("target_revision") == 1
    ]
    assert len(rollback_records) == 1
    # Journal v1 keeps the legacy stored kind so a release downgrade can still
    # read/recover this transaction. Public history derives rollback semantics
    # from target_revision instead of changing the durable v1 discriminator.
    assert rollback_records[0]["kind"] == "configuration_apply"
    assert rollback_records[0]["target_revision"] == 1
    assert rollback_records[0]["base_revision"] == 2
    assert rollback_records[0]["candidate_revision"] == 3
    projected = next(
        item
        for item in engine.history_page(limit=10)["items"]
        if item["operation_id"] == result["operation_id"]
    )
    assert projected["kind"] == "configuration_rollback"
    assert projected["target_revision"] == 1


def test_rollback_to_revision_zero_resets_operator_layer(tmp_path: Path) -> None:
    store, resolver, runtime, _, engine = _components(tmp_path)
    _apply(engine, 0, 3456)

    plan = engine.rollback_plan(base_revision=1, target_revision=0)
    assert plan["changes"] == [
        {
            "key": "streaming.max_chunks",
            "operation": "reset",
            "operator_before": 3456,
            "operator_after": None,
            "effective_before": 3456,
            "effective_after": resolver.repository_defaults["streaming.max_chunks"],
            "effective_source_before": "operator",
            "effective_source_after": "repository",
            "operator_override_shadowed_after": False,
            "apply_mode": "hot_reload",
            "risk": "medium",
            "no_effective_change": False,
        }
    ]

    result = engine.rollback(
        expected_revision=1,
        target_revision=0,
        plan_digest=plan["plan_digest"],
        actor=_ACTOR,
        request_id="req_rollback_to_zero",
    )
    assert result["revision"] == 2
    assert store.read().overrides == {}
    assert runtime.snapshot().streaming_max_chunks == resolver.repository_defaults["streaming.max_chunks"]


def test_rollback_rejects_stale_current_and_unknown_target(tmp_path: Path) -> None:
    _, _, _, _, engine = _components(tmp_path)
    _apply(engine, 0, 4567)

    with pytest.raises(ConfigurationRevisionConflict) as current:
        engine.rollback_plan(base_revision=1, target_revision=1)
    assert current.value.reason == "rollback_already_current"

    with pytest.raises(ConfigurationRevisionConflict) as unknown:
        engine.rollback_plan(base_revision=1, target_revision=99)
    assert unknown.value.reason == "history_revision_not_found"

    with pytest.raises(ConfigurationRevisionConflict) as stale:
        engine.rollback_plan(base_revision=0, target_revision=0)
    assert stale.value.current_revision == 1


def test_conflicting_snapshot_evidence_fails_closed(tmp_path: Path) -> None:
    _, _, _, history, engine = _components(tmp_path)
    _apply(engine, 0, 5100)

    changes = _change(5200)
    plan = engine.plan(base_revision=1, changes=changes)
    fake_operation = history.begin(
        plan=plan,
        overrides_before={"streaming.max_chunks": 9999},
        overrides_after={"streaming.max_chunks": 5200},
        actor=_ACTOR,
        request_id="req_conflicting_evidence",
    )
    history.finish(
        fake_operation,
        status="rejected",
        phase="persisting",
        applied_revision=1,
        verification={
            "store_revision": 1,
            "resolver_revision": 1,
            "runtime_revision": 1,
            "synchronized": True,
        },
        error="synthetic conflict",
    )

    applied = engine.apply(
        expected_revision=1,
        changes=changes,
        plan_digest=plan["plan_digest"],
        actor=_ACTOR,
        request_id="req_real_revision_2",
    )
    assert applied["revision"] == 2

    with pytest.raises(ConfigurationWriteUnavailable) as conflict:
        engine.rollback_plan(base_revision=2, target_revision=1)
    assert conflict.value.reason == "history_revision_conflict"
