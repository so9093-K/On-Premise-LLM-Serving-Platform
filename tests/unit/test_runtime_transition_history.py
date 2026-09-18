from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ai_model_serving.runtime_transition_history import (
    RuntimeTransitionHistoryCursorError,
    RuntimeTransitionHistoryStore,
    RuntimeTransitionHistoryUnavailable,
)


def _begin(store: RuntimeTransitionHistoryStore, *, request_id: str = "req-runtime-1") -> str:
    return store.begin(
        service_key="embedding_ko",
        desired_state="active",
        force=True,
        plan_digest="a" * 64,
        actor={"auth_method": "api_key", "actor_id": "admin-key:deadbeef"},
        request_id=request_id,
        before={"desired_state": "stopped", "observed_state": "exited"},
    )


def test_runtime_transition_history_persists_and_reloads_terminal_record(tmp_path: Path) -> None:
    directory = tmp_path / "runtime-history"
    store = RuntimeTransitionHistoryStore(directory)
    operation_id = _begin(store)
    finished = store.finish(
        operation_id,
        status="verified",
        phase="completed",
        apply_result={"started": ["embedding_ko"], "evicted": ["embedding"]},
        verification={"converged": True, "observed_state": "running"},
    )

    assert finished["durable"] is True
    reloaded = RuntimeTransitionHistoryStore(directory)
    record = reloaded.get(operation_id)
    assert record["status"] == "verified"
    assert record["plan_digest"] == "a" * 64
    assert record["verification"]["converged"] is True


def test_runtime_transition_history_marks_pending_record_interrupted_after_restart(tmp_path: Path) -> None:
    directory = tmp_path / "runtime-history"
    operation_id = _begin(RuntimeTransitionHistoryStore(directory))

    restarted = RuntimeTransitionHistoryStore(directory)
    assert restarted.recover_interrupted_operations() == 1
    record = RuntimeTransitionHistoryStore(directory).get(operation_id)

    assert record["status"] == "interrupted_after_restart"
    assert record["phase"] == "interrupted"
    assert record["verification"] == {
        "converged": False,
        "reason": "process_restarted_before_terminal_record",
    }


def test_runtime_transition_history_cursor_pages_newest_first(tmp_path: Path) -> None:
    store = RuntimeTransitionHistoryStore(tmp_path / "runtime-history")
    first = _begin(store, request_id="req-1")
    store.finish(
        first,
        status="noop",
        phase="completed",
        apply_result={"changed": False},
        verification={"converged": True},
    )
    second = _begin(store, request_id="req-2")
    store.finish(
        second,
        status="verified",
        phase="completed",
        apply_result={"changed": True},
        verification={"converged": True},
    )

    page1 = store.page(limit=1)
    assert [item["operation_id"] for item in page1["items"]] == [second]
    assert page1["next_cursor"]
    page2 = store.page(limit=1, cursor=page1["next_cursor"])
    assert [item["operation_id"] for item in page2["items"]] == [first]
    assert page2["next_cursor"] is None

    with pytest.raises(RuntimeTransitionHistoryCursorError):
        store.page(limit=1, cursor="not-a-cursor")


def test_runtime_transition_history_revalidates_when_directory_changes(tmp_path: Path) -> None:
    directory = tmp_path / "runtime-history"
    store = RuntimeTransitionHistoryStore(directory)
    assert store.page(limit=10)["items"] == []

    (directory / f"rt_{'f' * 32}.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(RuntimeTransitionHistoryUnavailable) as captured:
        store.page(limit=10)
    assert captured.value.reason == "history_unreadable"


def test_runtime_transition_history_rejects_extra_persisted_fields(tmp_path: Path) -> None:
    directory = tmp_path / "runtime-history"
    store = RuntimeTransitionHistoryStore(directory)
    operation_id = _begin(store)
    record_path = directory / f"{operation_id}.json"
    document = json.loads(record_path.read_text(encoding="utf-8"))
    document["unexpected"] = "must-fail-closed"
    replacement = directory / ".external-replacement.tmp"
    replacement.write_text(json.dumps(document), encoding="utf-8")
    replacement.replace(record_path)

    with pytest.raises(RuntimeTransitionHistoryUnavailable) as captured:
        store.get(operation_id)
    assert captured.value.reason == "history_invalid"


def test_runtime_transition_history_fails_closed_when_directory_disappears(tmp_path: Path) -> None:
    directory = tmp_path / "runtime-history"
    store = RuntimeTransitionHistoryStore(directory)
    assert store.page(limit=10)["items"] == []
    shutil.rmtree(directory)

    with pytest.raises(RuntimeTransitionHistoryUnavailable) as captured:
        store.page(limit=10)
    assert captured.value.reason == "history_unreadable"


def test_runtime_transition_history_local_fallback_is_explicitly_non_durable() -> None:
    store = RuntimeTransitionHistoryStore(None)
    operation_id = _begin(store)
    record = store.finish(
        operation_id,
        status="noop",
        phase="completed",
        apply_result={"changed": False},
        verification={"converged": None, "reason": "runtime_controller_unconfigured"},
    )

    assert record["durable"] is False
