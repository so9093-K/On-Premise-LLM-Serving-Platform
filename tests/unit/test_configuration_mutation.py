from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_model_serving.configuration_contract import (
    ConfigurationApplyFailure,
    ConfigurationPreconditionRequired,
    ConfigurationRevisionConflict,
    ConfigurationValidationError,
    ConfigurationWriteUnavailable,
    parse_configuration_if_match,
)
from ai_model_serving.configuration_history import ConfigurationHistoryStore
from ai_model_serving.configuration_mutation import ConfigurationMutationEngine
from ai_model_serving.configuration_plane import configuration_schema_items
from ai_model_serving.operator_configuration import (
    ConfigurationValueResolver,
    OperatorConfigurationState,
    OperatorConfigurationStore,
    operator_metadata_by_key,
    repository_operator_defaults,
    runtime_snapshot_from_resolver,
)
from ai_model_serving.runtime_configuration import RuntimeConfigurationProvider


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
    return schema_items, store, resolver, runtime, history, engine


def _change(value: int) -> list[dict]:
    return [{"key": "streaming.max_chunks", "op": "set", "value": value}]


def test_plan_apply_and_reset_share_one_revision_boundary(tmp_path: Path) -> None:
    _, store, resolver, runtime, _, engine = _components(tmp_path)

    plan = engine.plan(base_revision=0, changes=_change(1234))
    assert plan["base_revision"] == 0
    assert plan["candidate_revision"] == 1
    assert plan["would_change"] is True
    assert len(plan["plan_digest"]) == 64
    assert plan["changes"] == [
        {
            "key": "streaming.max_chunks",
            "operation": "set",
            "operator_before": None,
            "operator_after": 1234,
            "effective_before": resolver.repository_defaults["streaming.max_chunks"],
            "effective_after": 1234,
            "effective_source_before": "repository",
            "effective_source_after": "operator",
            "operator_override_shadowed_after": False,
            "apply_mode": "hot_reload",
            "risk": "medium",
            "no_effective_change": False,
        }
    ]

    result = engine.apply(
        expected_revision=0,
        changes=_change(1234),
        plan_digest=plan["plan_digest"],
        actor={"auth_method": "local", "actor_id": "local_operator"},
        request_id="req_test",
    )
    assert result["status"] == "verified"
    assert result["changed"] is True
    assert result["revision"] == 1
    assert result["verification"]["synchronized"] is True
    assert store.read().overrides == {"streaming.max_chunks": 1234}
    assert resolver.revision == 1
    assert runtime.snapshot().revision == 1
    assert runtime.snapshot().streaming_max_chunks == 1234

    reset_changes = [{"key": "streaming.max_chunks", "op": "reset"}]
    reset_plan = engine.plan(base_revision=1, changes=reset_changes)
    reset = reset_plan["changes"][0]
    assert reset["operator_before"] == 1234
    assert reset["operator_after"] is None
    assert reset["effective_source_after"] == "repository"

    reset_result = engine.apply(
        expected_revision=1,
        changes=reset_changes,
        plan_digest=reset_plan["plan_digest"],
        actor={"auth_method": "local", "actor_id": "local_operator"},
        request_id="req_reset",
    )
    assert reset_result["revision"] == 2
    assert store.read().overrides == {}
    assert runtime.snapshot().streaming_max_chunks == resolver.repository_defaults["streaming.max_chunks"]


def test_noop_does_not_advance_configuration_revision(tmp_path: Path) -> None:
    _, store, _, runtime, history, engine = _components(tmp_path)
    default = runtime.snapshot().streaming_max_chunks
    changes = [{"key": "streaming.max_chunks", "op": "reset"}]

    plan = engine.plan(base_revision=0, changes=changes)
    assert plan["would_change"] is False
    assert plan["candidate_revision"] == 0
    assert plan["changes"][0]["effective_before"] == default
    assert plan["changes"][0]["no_effective_change"] is True

    result = engine.apply(
        expected_revision=0,
        changes=changes,
        plan_digest=plan["plan_digest"],
        actor={"auth_method": "local", "actor_id": "local_operator"},
        request_id="req_noop",
    )
    assert result["changed"] is False
    assert result["revision"] == 0
    assert store.read().revision == 0
    records = list(history.directory.glob("cfg_*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["status"] == "noop"
    assert record["overrides_before"] == {}
    assert record["overrides_after"] == {}


def test_stale_revision_and_plan_digest_are_fail_closed(tmp_path: Path) -> None:
    _, _, _, _, _, engine = _components(tmp_path)
    plan = engine.plan(base_revision=0, changes=_change(2222))

    with pytest.raises(ConfigurationRevisionConflict, match="no longer matches") as mismatch:
        engine.apply(
            expected_revision=0,
            changes=_change(2222),
            plan_digest="0" * 64,
            actor={"auth_method": "local", "actor_id": "local_operator"},
            request_id="req_digest",
        )
    assert mismatch.value.reason == "plan_digest_mismatch"

    applied = engine.apply(
        expected_revision=0,
        changes=_change(2222),
        plan_digest=plan["plan_digest"],
        actor={"auth_method": "local", "actor_id": "local_operator"},
        request_id="req_apply",
    )
    assert applied["revision"] == 1

    with pytest.raises(ConfigurationRevisionConflict, match="expected 0, current 1"):
        engine.plan(base_revision=0, changes=_change(3333))


def test_non_editable_and_invalid_values_are_rejected_before_persistence(tmp_path: Path) -> None:
    _, store, _, _, _, engine = _components(tmp_path)

    with pytest.raises(ConfigurationValidationError, match="not editable"):
        engine.plan(
            base_revision=0,
            changes=[{"key": "security.auth_mode", "op": "set", "value": "strict"}],
        )
    with pytest.raises(ConfigurationValidationError, match="below metadata minimum"):
        engine.plan(
            base_revision=0,
            changes=[
                {
                    "key": "operational.max_retrieval_documents",
                    "op": "set",
                    "value": 0,
                }
            ],
        )
    assert store.read() == OperatorConfigurationState(revision=0, overrides={})


def test_apply_failure_keeps_durable_desired_state_and_blocks_more_writes(tmp_path: Path) -> None:
    schema_items, store, resolver, runtime, history, engine = _components(tmp_path)
    plan = engine.plan(base_revision=0, changes=_change(4321))

    original_install = runtime.install

    def fail_install(snapshot):
        raise RuntimeError("simulated runtime install failure")

    runtime.install = fail_install  # type: ignore[method-assign]
    with pytest.raises(ConfigurationApplyFailure, match="configuration apply did not complete"):
        engine.apply(
            expected_revision=0,
            changes=_change(4321),
            plan_digest=plan["plan_digest"],
            actor={"auth_method": "local", "actor_id": "local_operator"},
            request_id="req_failure",
        )
    runtime.install = original_install  # type: ignore[method-assign]

    # Persist-first: desired state is durable even though the in-process runtime
    # snapshot failed to converge. A second write is blocked instead of hiding drift.
    persisted = store.read()
    assert persisted.revision == 1
    assert persisted.overrides == {"streaming.max_chunks": 4321}
    assert resolver.revision == 1
    assert runtime.snapshot().revision == 0
    assert engine.status()["reason"] == "state_not_synchronized"
    with pytest.raises(ConfigurationWriteUnavailable, match="not synchronized"):
        engine.plan(base_revision=1, changes=_change(5000))

    records = list(history.directory.glob("cfg_*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["status"] == "apply_failed"
    assert record["applied_revision"] == 1
    assert record["overrides_before"] == {}
    assert record["overrides_after"] == {"streaming.max_chunks": 4321}

    # Restart semantics: startup reads persisted desired state first, then hydrates
    # resolver/runtime from it. The previous failed operation remains an audit record
    # but the write plane becomes synchronized again.
    restart_resolver = ConfigurationValueResolver(
        repository_defaults=repository_operator_defaults(schema_items),
        operator_state=persisted,
    )
    restart_runtime = RuntimeConfigurationProvider(
        runtime_snapshot_from_resolver(restart_resolver, schema_items)
    )
    restart_engine = ConfigurationMutationEngine(
        schema_items=schema_items,
        store=store,
        resolver=restart_resolver,
        runtime_configuration=restart_runtime,
        history=history,
    )
    restart_engine.recover_interrupted_operations()
    assert restart_engine.status()["available"] is True
    assert restart_runtime.snapshot().streaming_max_chunks == 4321


def test_pending_journal_is_reconciled_only_when_persisted_snapshot_matches(tmp_path: Path) -> None:
    schema_items, store, _, _, history, engine = _components(tmp_path)
    plan = engine.plan(base_revision=0, changes=_change(2468))
    operation_id = history.begin(
        plan=plan,
        overrides_before={},
        overrides_after={"streaming.max_chunks": 2468},
        actor={"auth_method": "local", "actor_id": "local_operator"},
        request_id="req_crash",
    )

    # Simulate crash after persistence but before resolver/runtime/history completion.
    persisted = store.replace({"streaming.max_chunks": 2468}, expected_revision=0)
    resolver = ConfigurationValueResolver(
        repository_defaults=repository_operator_defaults(schema_items),
        operator_state=persisted,
    )
    runtime = RuntimeConfigurationProvider(runtime_snapshot_from_resolver(resolver, schema_items))
    recovered = ConfigurationMutationEngine(
        schema_items=schema_items,
        store=store,
        resolver=resolver,
        runtime_configuration=runtime,
        history=history,
    )
    recovered.recover_interrupted_operations()

    assert history.pending_records() == []
    record = json.loads((history.directory / f"{operation_id}.json").read_text(encoding="utf-8"))
    assert record["status"] == "recovered_after_restart"
    assert record["applied_revision"] == 1
    assert recovered.status()["available"] is True


def test_pending_journal_does_not_recover_different_state_with_same_revision(tmp_path: Path) -> None:
    schema_items, store, _, _, history, engine = _components(tmp_path)
    plan = engine.plan(base_revision=0, changes=_change(2468))
    operation_id = history.begin(
        plan=plan,
        overrides_before={},
        overrides_after={"streaming.max_chunks": 2468},
        actor={"auth_method": "local", "actor_id": "local_operator"},
        request_id="req_race",
    )

    # Same candidate revision but different desired content must never be called a
    # successful recovery. This is the race revision-only recovery cannot distinguish.
    persisted = store.replace({"streaming.max_chunks": 9999}, expected_revision=0)
    resolver = ConfigurationValueResolver(
        repository_defaults=repository_operator_defaults(schema_items),
        operator_state=persisted,
    )
    runtime = RuntimeConfigurationProvider(runtime_snapshot_from_resolver(resolver, schema_items))
    recovered = ConfigurationMutationEngine(
        schema_items=schema_items,
        store=store,
        resolver=resolver,
        runtime_configuration=runtime,
        history=history,
    )
    recovered.recover_interrupted_operations()

    record = json.loads((history.directory / f"{operation_id}.json").read_text(encoding="utf-8"))
    assert record["status"] == "interrupted_state_mismatch"
    assert record["applied_revision"] == 1


def test_if_match_parser_requires_exact_configuration_etag() -> None:
    assert parse_configuration_if_match('"config-17"') == 17
    with pytest.raises(ConfigurationPreconditionRequired, match="If-Match header"):
        parse_configuration_if_match(None)
    with pytest.raises(ConfigurationPreconditionRequired, match="ETag format"):
        parse_configuration_if_match("17")


def test_status_does_not_rescan_terminal_history(tmp_path: Path) -> None:
    _, _, _, _, history, engine = _components(tmp_path)
    plan = engine.plan(base_revision=0, changes=_change(3210))
    engine.apply(
        expected_revision=0,
        changes=_change(3210),
        plan_digest=plan["plan_digest"],
        actor={"auth_method": "local", "actor_id": "local_operator"},
        request_id="req_status_index",
    )

    def fail_full_history_scan():
        raise AssertionError("write readiness must not scan terminal history")

    history.records = fail_full_history_scan  # type: ignore[method-assign]
    status = engine.status()
    assert status["available"] is True
    assert status["pending_operations"] == 0


def test_pending_index_rebuilds_from_durable_journal(tmp_path: Path) -> None:
    _, _, _, _, history, engine = _components(tmp_path)
    plan = engine.plan(base_revision=0, changes=_change(2468))
    operation_id = history.begin(
        plan=plan,
        overrides_before={},
        overrides_after={"streaming.max_chunks": 2468},
        actor={"auth_method": "local", "actor_id": "local_operator"},
        request_id="req_pending_index",
    )

    reloaded = ConfigurationHistoryStore(history.directory)
    assert reloaded.pending_count == 1
    assert [record["operation_id"] for record in reloaded.pending_records()] == [operation_id]


def test_pending_index_revalidates_when_history_directory_changes(tmp_path: Path) -> None:
    _, _, _, _, history, _ = _components(tmp_path)
    assert history.pending_count == 0

    corrupt = history.directory / f"cfg_{'f' * 32}.json"
    corrupt.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ConfigurationWriteUnavailable) as exc_info:
        _ = history.pending_count
    assert exc_info.value.reason == "history_unreadable"


def test_pending_index_fails_closed_when_history_directory_disappears(tmp_path: Path) -> None:
    _, _, _, _, history, _ = _components(tmp_path)
    assert history.pending_count == 0
    history.directory.rmdir()

    with pytest.raises(ConfigurationWriteUnavailable) as exc_info:
        _ = history.pending_count
    assert exc_info.value.reason == "history_unreadable"
