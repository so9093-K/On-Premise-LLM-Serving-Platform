from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from ai_model_serving.main_model_control import (
    MainModelConfigurationError,
    MainModelManager,
    MainModelStateError,
    MainModelStateStore,
    MainModelSwitchError,
    load_main_model_catalog,
    resolve_boot_profile,
)

ROOT = Path(__file__).resolve().parents[2]


class FakeBackend:
    def __init__(
        self,
        observed: str | None = None,
        fail_profile: str | None = None,
        fail_drain: bool = False,
        fail_prepare: bool = False,
        started_at: str | None = "boot-0",
    ) -> None:
        self.observed = observed
        self.fail_profile = fail_profile
        self.fail_drain = fail_drain
        self.fail_prepare = fail_prepare
        self.prepared: list[str] = []
        self.replaced: list[str] = []
        self.drain_calls = 0
        self.stopped = 0
        self.started = 0
        self.running = observed is not None
        # 컨테이너의 관측된 Docker State.StartedAt을 흉내낸다. 테스트가 외부에서
        # main-llm-vllm이 재시작된 상황(admin-sidecar 제어 API를 안 거친 경우)을
        # 시뮬레이션하려면 validate() 호출 사이에 이 값을 바꿔주면 된다.
        self.started_at = started_at
        self.observed_started_at_calls = 0
        self.validate_calls: list[str] = []

    async def observed_profile(self, catalog):
        return self.observed

    async def stop(self, catalog):
        self.stopped += 1
        self.running = False

    async def start(self, catalog):
        self.started += 1
        self.running = True

    async def is_running(self, catalog):
        return self.running

    async def prepare(self, catalog, profile):
        self.prepared.append(profile.profile_id)
        if self.fail_prepare:
            raise RuntimeError("cache prepare failed")

    async def replace(self, catalog, profile):
        self.replaced.append(profile.profile_id)
        self.observed = profile.profile_id

    async def wait_for_drain(self, timeout_seconds):
        self.drain_calls += 1
        if self.fail_drain:
            raise RuntimeError("drain timed out")

    async def validate(self, catalog, profile):
        self.validate_calls.append(profile.profile_id)
        if profile.profile_id == self.fail_profile:
            raise RuntimeError("validation failed")

    async def observed_started_at(self, catalog):
        self.observed_started_at_calls += 1
        return self.started_at


def catalog():
    result = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    result.runtime["drain_timeout_seconds"] = 0
    return result


def test_real_catalog_profile_views_are_json_serializable() -> None:
    # The admin-sidecar GET /main-model serializes profile snapshots with the
    # stdlib JSON encoder. A non-serializable value (e.g. an unquoted YAML date
    # parsed as datetime.date) makes that endpoint return 500, which the Gateway
    # reads as SidecarUnavailable and then 503s every main-model request — a
    # deploy-breaking failure. Guard the shipped catalog directly.
    loaded = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    for profile in loaded.profiles.values():
        json.dumps(profile.public_view())


def test_main_model_snapshot_serializes_through_endpoint_encoder(tmp_path) -> None:
    # Covers the exact admin-sidecar /main-model serialization path: the endpoint
    # returns jsonable_encoder(manager.snapshot()). This is the path that actually
    # 500'd in production (a date in the active profile's compatibility block), and
    # is the boundary the jsonable_encoder hardening protects. Built from the real
    # shipped catalog so a regression in either the data or the encoding is caught.
    from fastapi.encoders import jsonable_encoder

    loaded = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    state = store.read()
    state.update(active_profile=loaded.default_profile, gate="open")
    store.write(state)
    manager = MainModelManager(loaded, store, FakeBackend(loaded.default_profile))
    json.dumps(jsonable_encoder(manager.snapshot()))


def wait_for_operation(manager: MainModelManager, operation_id: str) -> dict:
    async def wait():
        for _ in range(100):
            operation = manager.operation(operation_id)
            if operation and operation["status"] in {"completed", "failed", "rollback_failed"}:
                return operation
            await asyncio.sleep(0.01)
        raise AssertionError("operation did not finish")

    return asyncio.run(wait())


def test_profile_catalog_is_pinned_and_preserves_public_alias():
    loaded = catalog()
    assert set(loaded.profiles) == {
        "gemma4-26b-a4b-fp8",
        "gemma4-12b-unified-fp8",
    }
    assert loaded.public_model == "local-main"
    assert "@sha256:" in loaded.runtime["image"]
    for profile in loaded.profiles.values():
        assert profile.served_model_name == "local-main"
        assert len(profile.revision) == 40


def test_boot_precedence_and_lock():
    loaded = catalog()
    assert resolve_boot_profile(
        loaded,
        configured_profile="gemma4-26b-a4b-fp8",
        locked=False,
        persisted_profile="gemma4-12b-unified-fp8",
    ) == "gemma4-12b-unified-fp8"
    assert resolve_boot_profile(
        loaded,
        configured_profile="gemma4-26b-a4b-fp8",
        locked=True,
        persisted_profile="gemma4-12b-unified-fp8",
    ) == "gemma4-26b-a4b-fp8"
    with pytest.raises(MainModelConfigurationError):
        resolve_boot_profile(
            loaded,
            configured_profile="missing",
            locked=True,
            persisted_profile=None,
        )


@contextmanager
def _collect_records(logger_name: str):
    # service_logger()가 propagate=False로 설정하므로(logging_policy.py)
    # caplog도 capsys/capfd도 이 로거의 레코드를 못 잡는다 — caplog는 root
    # logger로의 propagation에 의존하고, 모듈 레벨 StreamHandler는 import
    # 시점에 sys.stderr를 한 번 바인딩해서 테스트별 capture fixture보다 먼저
    # 고정돼버린다. 대신 이 로거에 핸들러를 직접 붙인다.
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger(logger_name)
    handler = _ListHandler()
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def test_manager_init_warns_when_persisted_profile_overrides_configured(tmp_path):
    # 실제 장애 상황 회귀 테스트: persisted active_profile이 MAIN_LLM_BOOT_PROFILE과
    # 달라 재시작 시 서빙 모델이 바뀌었는데 운영자가 눈치챌 방법이 없었다.
    # resolve_boot_profile의 우선순위(persisted가 configured를 이긴다 — ADR-0017
    # "부트 우선순위", test_boot_precedence_and_lock 참고)는 의도된 설계다 — 마지막
    # 운영 결정을 재시작 후에도 유지하기 위함. 다만 그 사실이 조용히 넘어가면 안 된다.
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    state = store.read()
    state["active_profile"] = "gemma4-12b-unified-fp8"
    store.write(state)

    with _collect_records("ai_model_serving.main_model_control") as records:
        manager = MainModelManager(
            loaded,
            store,
            FakeBackend(),
            boot_profile="gemma4-26b-a4b-fp8",
        )

    assert manager.boot_profile == "gemma4-12b-unified-fp8"
    assert any("boot profile diverges" in record.getMessage() for record in records)


def test_manager_init_locked_boot_profile_does_not_warn(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    state = store.read()
    state["active_profile"] = "gemma4-12b-unified-fp8"
    store.write(state)

    with _collect_records("ai_model_serving.main_model_control") as records:
        manager = MainModelManager(
            loaded,
            store,
            FakeBackend(),
            boot_profile="gemma4-26b-a4b-fp8",
            profile_locked=True,
        )

    assert manager.boot_profile == "gemma4-26b-a4b-fp8"
    assert not any("boot profile diverges" in record.getMessage() for record in records)


def test_state_store_atomic_round_trip_and_corruption(tmp_path):
    store = MainModelStateStore(tmp_path / "state.json", "gemma4-26b-a4b-fp8")
    state = store.read()
    state["active_profile"] = "gemma4-26b-a4b-fp8"
    store.write(state)
    assert store.read()["active_profile"] == "gemma4-26b-a4b-fp8"
    store.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(MainModelStateError):
        store.read()
    quarantined = store.quarantine_corrupt_state("corrupt test state")
    assert quarantined is not None and quarantined.exists()
    assert store.read()["gate"] == "closed"
    assert store.read()["state_recovery_error"] == "corrupt test state"
    assert not list(tmp_path.glob("*.tmp"))


def test_state_store_update_does_not_lose_concurrent_process_updates(tmp_path):
    import multiprocessing

    path = tmp_path / "state.json"
    store = MainModelStateStore(path, "gemma4-26b-a4b-fp8")
    store.write(store.read())

    def increment(state_path: str, count: int) -> None:
        child = MainModelStateStore(Path(state_path), "gemma4-26b-a4b-fp8")
        for _ in range(count):
            child.update(
                lambda state: state.setdefault("stats", {}).update(
                    switch_requests=int(
                        state.setdefault("stats", {}).get("switch_requests", 0)
                    )
                    + 1
                )
            )

    processes = [
        multiprocessing.Process(target=increment, args=(str(path), 25))
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert store.read()["stats"]["switch_requests"] == 100


def test_unverified_switch_requires_confirmation(tmp_path):
    loaded = catalog()
    profile = loaded.profiles["gemma4-12b-unified-fp8"]
    loaded.profiles["gemma4-12b-unified-fp8"] = replace(
        profile,
        compatibility={**profile.compatibility, "status": "unknown"},
    )
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    manager = MainModelManager(loaded, store, FakeBackend(), boot_profile=loaded.default_profile)
    with pytest.raises(MainModelSwitchError) as error:
        manager.request_switch("gemma4-12b-unified-fp8")
    assert error.value.code == "MODEL_PROFILE_CONFIRMATION_REQUIRED"


def test_successful_switch_commits_only_after_validation(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    state = store.read()
    state.update(
        active_profile="gemma4-26b-a4b-fp8",
        last_known_good_profile="gemma4-26b-a4b-fp8",
        gate="open",
    )
    store.write(state)
    manager = MainModelManager(loaded, store, FakeBackend("gemma4-26b-a4b-fp8"))

    async def run():
        operation_id, _ = manager.request_switch("gemma4-12b-unified-fp8")
        while manager.operation(operation_id)["status"] not in {
            "completed",
            "failed",
            "rollback_failed",
        }:
            await asyncio.sleep(0.01)
        return operation_id

    operation_id = asyncio.run(run())
    assert manager.operation(operation_id)["status"] == "completed"
    assert manager.operation(operation_id)["stage"] == "completed"
    assert manager.snapshot()["active_profile"]["id"] == "gemma4-12b-unified-fp8"
    assert manager.snapshot()["gate"] == "open"


def test_failed_switch_rolls_back_without_silent_success(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    state = store.read()
    state.update(
        active_profile="gemma4-26b-a4b-fp8",
        last_known_good_profile="gemma4-26b-a4b-fp8",
        gate="open",
    )
    store.write(state)
    backend = FakeBackend(
        "gemma4-26b-a4b-fp8",
        fail_profile="gemma4-12b-unified-fp8",
    )
    manager = MainModelManager(loaded, store, backend)

    async def run():
        operation_id, _ = manager.request_switch("gemma4-12b-unified-fp8")
        while manager.operation(operation_id)["status"] not in {
            "completed",
            "failed",
            "rollback_failed",
        }:
            await asyncio.sleep(0.01)
        return operation_id

    operation_id = asyncio.run(run())
    assert manager.operation(operation_id)["status"] == "failed"
    assert backend.replaced == [
        "gemma4-12b-unified-fp8",
        "gemma4-26b-a4b-fp8",
    ]
    assert manager.snapshot()["active_profile"]["id"] == "gemma4-26b-a4b-fp8"
    assert manager.snapshot()["last_operation"]["error"] == "validation failed"


def test_drain_failure_preserves_current_runtime_without_replace(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    state = store.read()
    state.update(
        active_profile="gemma4-26b-a4b-fp8",
        last_known_good_profile="gemma4-26b-a4b-fp8",
        gate="open",
    )
    store.write(state)
    backend = FakeBackend("gemma4-26b-a4b-fp8", fail_drain=True)
    manager = MainModelManager(loaded, store, backend)

    async def run():
        operation_id, _ = manager.request_switch("gemma4-12b-unified-fp8")
        while manager.operation(operation_id)["status"] not in {
            "completed",
            "failed",
            "rollback_failed",
        }:
            await asyncio.sleep(0.01)
        return operation_id

    operation_id = asyncio.run(run())
    assert manager.operation(operation_id)["status"] == "failed"
    assert backend.replaced == []
    assert manager.snapshot()["active_profile"]["id"] == "gemma4-26b-a4b-fp8"
    assert manager.snapshot()["gate"] == "open"


def test_cache_prepare_failure_keeps_current_runtime_and_gate_open(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    state = store.read()
    state.update(
        active_profile="gemma4-26b-a4b-fp8",
        last_known_good_profile="gemma4-26b-a4b-fp8",
        gate="open",
    )
    store.write(state)
    backend = FakeBackend("gemma4-26b-a4b-fp8", fail_prepare=True)
    manager = MainModelManager(loaded, store, backend)

    async def run():
        operation_id, _ = manager.request_switch("gemma4-12b-unified-fp8")
        while manager.operation(operation_id)["status"] not in {
            "completed",
            "failed",
            "rollback_failed",
        }:
            await asyncio.sleep(0.01)
        return operation_id

    operation_id = asyncio.run(run())
    assert manager.operation(operation_id)["status"] == "failed"
    assert manager.operation(operation_id)["error"] == "cache prepare failed"
    assert backend.prepared == ["gemma4-12b-unified-fp8"]
    assert backend.replaced == []
    assert manager.snapshot()["active_profile"]["id"] == "gemma4-26b-a4b-fp8"
    assert manager.snapshot()["gate"] == "open"


def test_gate_stays_open_while_cache_prepare_is_running(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    state = store.read()
    state.update(
        active_profile="gemma4-26b-a4b-fp8",
        last_known_good_profile="gemma4-26b-a4b-fp8",
        gate="open",
    )
    store.write(state)

    class BlockingPrepareBackend(FakeBackend):
        def __init__(self):
            super().__init__("gemma4-26b-a4b-fp8")
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def prepare(self, catalog, profile):
            self.prepared.append(profile.profile_id)
            self.started.set()
            await self.release.wait()

    backend = BlockingPrepareBackend()
    manager = MainModelManager(loaded, store, backend)

    async def run():
        operation_id, _ = manager.request_switch("gemma4-12b-unified-fp8")
        await backend.started.wait()
        assert manager.operation(operation_id)["stage"] == "preparing"
        assert manager.snapshot()["gate"] == "open"
        backend.release.set()
        while manager.operation(operation_id)["status"] not in {
            "completed",
            "failed",
            "rollback_failed",
        }:
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert manager.snapshot()["gate"] == "open"


def test_request_id_retry_is_idempotent(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    manager = MainModelManager(loaded, store, FakeBackend())

    async def run():
        first = manager.request_switch(
            "gemma4-12b-unified-fp8",
            client_request_id="deploy-12b-1",
        )
        second = manager.request_switch(
            "gemma4-12b-unified-fp8",
            client_request_id="deploy-12b-1",
        )
        assert first.operation_id == second.operation_id
        assert first.reused is False
        assert second.reused is True
        while manager.operation(first.operation_id)["status"] not in {
            "completed",
            "failed",
            "rollback_failed",
        }:
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert len(store.read()["operations"]) == 1


def test_request_id_starts_fresh_switch_after_ttl_expires(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    # ttl=0 means a request_id expires as soon as its operation settles, so a
    # re-send of the same id is a new intent rather than a replay of the old one.
    manager = MainModelManager(
        loaded, store, FakeBackend(), idempotency_ttl_seconds=0.0
    )
    terminal = {"completed", "failed", "rollback_failed"}

    async def run():
        first = manager.request_switch(
            "gemma4-12b-unified-fp8",
            client_request_id="deploy-12b-1",
        )
        while manager.operation(first.operation_id)["status"] not in terminal:
            await asyncio.sleep(0.01)
        second = manager.request_switch(
            "gemma4-12b-unified-fp8",
            client_request_id="deploy-12b-1",
        )
        assert second.reused is False
        assert second.operation_id != first.operation_id
        while manager.operation(second.operation_id)["status"] not in terminal:
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert len(store.read()["operations"]) == 2


def test_restart_reconciles_interrupted_operation_on_requested_profile(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    now = 1.0
    operation = {
        "id": "00000000-0000-0000-0000-000000000001",
        "requested_profile": "gemma4-12b-unified-fp8",
        "previous_profile": "gemma4-26b-a4b-fp8",
        "status": "validating",
        "stage": "validating",
        "error": None,
        "rollback_error": None,
        "created_at": now,
        "updated_at": now,
        "boot_reconcile": False,
        "client_request_id": None,
    }
    state = store.read()
    state.update(
        active_profile="gemma4-26b-a4b-fp8",
        last_known_good_profile="gemma4-26b-a4b-fp8",
        gate="closed",
        last_operation=operation,
        operations=[operation],
    )
    store.write(state)
    manager = MainModelManager(
        loaded, store, FakeBackend("gemma4-12b-unified-fp8")
    )
    asyncio.run(manager.initialize())
    assert manager.snapshot()["active_profile"]["id"] == "gemma4-12b-unified-fp8"
    assert manager.snapshot()["gate"] == "open"
    assert manager.operation(operation["id"])["recovered_after_restart"] is True


def test_locked_manager_rejects_runtime_change(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    manager = MainModelManager(
        loaded,
        store,
        FakeBackend(),
        boot_profile=loaded.default_profile,
        profile_locked=True,
    )
    with pytest.raises(MainModelSwitchError) as error:
        manager.request_switch("gemma4-26b-a4b-fp8")
    assert error.value.code == "MODEL_PROFILE_LOCKED"


def test_initialize_reconciles_persisted_profile_in_background(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    state = store.read()
    state.update(
        active_profile="gemma4-12b-unified-fp8",
        last_known_good_profile="gemma4-12b-unified-fp8",
        gate="open",
    )
    store.write(state)
    backend = FakeBackend("gemma4-26b-a4b-fp8")
    manager = MainModelManager(loaded, store, backend)

    async def run():
        await manager.initialize()
        assert manager.snapshot()["gate"] == "closed"
        for _ in range(100):
            if manager.snapshot()["gate"] == "open":
                return
            await asyncio.sleep(0.01)
        raise AssertionError("boot reconciliation did not finish")

    asyncio.run(run())
    assert manager.snapshot()["active_profile"]["id"] == "gemma4-12b-unified-fp8"


def test_stop_main_drains_and_marks_stopped(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    store.write({**store.read(), "active_profile": loaded.default_profile, "gate": "open"})
    backend = FakeBackend(loaded.default_profile)
    manager = MainModelManager(loaded, store, backend)

    asyncio.run(manager.stop_main())

    assert backend.drain_calls == 1
    assert backend.stopped == 1
    assert backend.running is False
    snap = manager.snapshot()
    assert snap["runtime_state"] == "stopped"
    assert snap["gate"] == "closed"


def test_start_main_starts_validates_and_opens_gate(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    store.write({
        **store.read(),
        "active_profile": loaded.default_profile,
        "gate": "closed",
        "runtime_state": "stopped",
    })
    backend = FakeBackend(loaded.default_profile)
    backend.running = False
    manager = MainModelManager(loaded, store, backend)

    asyncio.run(manager.start_main())

    assert backend.started == 1
    assert backend.running is True
    snap = manager.snapshot()
    assert snap["runtime_state"] == "active"
    assert snap["gate"] == "open"


def test_start_main_recreates_container_when_observed_profile_does_not_match(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    store.write({
        **store.read(),
        "active_profile": "gemma4-12b-unified-fp8",
        "gate": "closed",
        "runtime_state": "stopped",
    })
    backend = FakeBackend("gemma4-26b-a4b-fp8")
    backend.running = False
    manager = MainModelManager(loaded, store, backend)

    asyncio.run(manager.start_main())

    assert backend.started == 1
    assert backend.replaced == ["gemma4-12b-unified-fp8"]
    snap = manager.snapshot()
    assert snap["active_profile"]["id"] == "gemma4-12b-unified-fp8"
    assert snap["runtime_state"] == "active"
    assert snap["gate"] == "open"


def _active_store(tmp_path, loaded, *, started_at: str | None = "boot-0"):
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    store.write({
        **store.read(),
        "active_profile": loaded.default_profile,
        "gate": "open",
        "runtime_state": "active",
        "last_validated_container_started_at": started_at,
    })
    return store


def test_reconcile_if_restarted_noop_when_container_fingerprint_unchanged(tmp_path):
    loaded = catalog()
    store = _active_store(tmp_path, loaded, started_at="boot-0")
    backend = FakeBackend(loaded.default_profile, started_at="boot-0")
    manager = MainModelManager(loaded, store, backend)

    asyncio.run(manager.reconcile_if_restarted())

    assert backend.validate_calls == []
    assert manager.snapshot()["gate"] == "open"


def test_reconcile_if_restarted_records_baseline_without_warmup_on_first_observation(tmp_path):
    # A state file written before this feature existed has no
    # last_validated_container_started_at yet. The first tick must record a
    # baseline instead of treating "no prior fingerprint" as drift — otherwise
    # every existing deployment would get one spurious extra warmup on upgrade.
    loaded = catalog()
    store = _active_store(tmp_path, loaded, started_at=None)
    backend = FakeBackend(loaded.default_profile, started_at="boot-0")
    manager = MainModelManager(loaded, store, backend)

    asyncio.run(manager.reconcile_if_restarted())

    assert backend.validate_calls == []
    assert manager.snapshot()  # still readable
    state = store.read()
    assert state["last_validated_container_started_at"] == "boot-0"


def test_reconcile_if_restarted_rewarms_without_closing_gate_on_drift(tmp_path):
    # Simulates an operator running `docker restart main-llm-vllm` directly:
    # the container's observed StartedAt moves without this controller ever
    # calling start()/replace(). reconcile_if_restarted() must catch this,
    # re-run validate() (which re-triggers the structured-output/media
    # warmup), and must NOT close the gate while doing so.
    loaded = catalog()
    store = _active_store(tmp_path, loaded, started_at="boot-0")
    backend = FakeBackend(loaded.default_profile, started_at="boot-1")
    manager = MainModelManager(loaded, store, backend)

    asyncio.run(manager.reconcile_if_restarted())

    assert backend.validate_calls == [loaded.default_profile]
    state = store.read()
    assert state["last_validated_container_started_at"] == "boot-1"
    assert state["gate"] == "open"


def test_reconcile_if_restarted_skips_when_runtime_stopped(tmp_path):
    loaded = catalog()
    store = _active_store(tmp_path, loaded, started_at="boot-0")
    store.update(lambda s: s.update(runtime_state="stopped"))
    backend = FakeBackend(loaded.default_profile, started_at="boot-1")
    manager = MainModelManager(loaded, store, backend)

    asyncio.run(manager.reconcile_if_restarted())

    assert backend.observed_started_at_calls == 0
    assert backend.validate_calls == []


def test_reconcile_if_restarted_skips_while_operation_in_progress(tmp_path):
    loaded = catalog()
    store = _active_store(tmp_path, loaded, started_at="boot-0")
    store.update(lambda s: s.update(last_operation={"id": "op-1", "status": "starting"}))
    backend = FakeBackend(loaded.default_profile, started_at="boot-1")
    manager = MainModelManager(loaded, store, backend)

    asyncio.run(manager.reconcile_if_restarted())

    assert backend.validate_calls == []


def test_reconcile_if_restarted_propagates_validate_failure_without_updating_fingerprint(tmp_path):
    # A failing re-validate must not silently mark the new (still JIT-cold, or
    # possibly broken) container as validated — the caller (the background
    # reconciliation loop) is responsible for catching this and retrying on
    # the next tick.
    loaded = catalog()
    store = _active_store(tmp_path, loaded, started_at="boot-0")
    backend = FakeBackend(
        loaded.default_profile, started_at="boot-1", fail_profile=loaded.default_profile
    )
    manager = MainModelManager(loaded, store, backend)

    with pytest.raises(RuntimeError, match="validation failed"):
        asyncio.run(manager.reconcile_if_restarted())

    assert store.read()["last_validated_container_started_at"] == "boot-0"


def test_initialize_and_reconcile_do_not_validate_concurrently(tmp_path):
    # Regression guard: initialize() used to run unlocked, so a reconcile tick
    # firing while a slow boot-time validate() was still in flight could kick
    # off a second, fully concurrent validate() against the same container.
    # Both now take self._lock, so at most one backend.validate() call is ever
    # in flight at a time. Whichever of the two runs second also benefits from
    # re-reading state *after* acquiring the lock: by the time it gets in, the
    # first one has already recorded the new container fingerprint, so it sees
    # no more drift and skips re-validating entirely — proving both mutual
    # exclusion (max_concurrent == 1) and no redundant duplicate work
    # (validate() ends up called exactly once, not twice).
    loaded = catalog()
    store = _active_store(tmp_path, loaded, started_at="boot-0")
    backend = FakeBackend(loaded.default_profile, started_at="boot-1")
    manager = MainModelManager(loaded, store, backend)

    concurrent = 0
    max_concurrent = 0
    real_validate = backend.validate

    async def tracking_validate(catalog_, profile):
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.02)  # force an await point where an unlocked race would show up
        await real_validate(catalog_, profile)
        concurrent -= 1

    backend.validate = tracking_validate

    async def run_both():
        await asyncio.gather(manager.initialize(), manager.reconcile_if_restarted())

    asyncio.run(run_both())

    assert len(backend.validate_calls) == 1
    assert max_concurrent == 1
    assert store.read()["last_validated_container_started_at"] == "boot-1"


def test_initialize_respects_deliberate_stop(tmp_path):
    loaded = catalog()
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    store.write({
        **store.read(),
        "active_profile": loaded.default_profile,
        "gate": "closed",
        "runtime_state": "stopped",
    })
    backend = FakeBackend(loaded.default_profile)
    manager = MainModelManager(loaded, store, backend)

    asyncio.run(manager.initialize())

    # A deliberately-stopped main is not auto-started on boot.
    assert backend.started == 0
    assert backend.replaced == []
    assert manager.snapshot()["gate"] == "closed"
    assert manager.snapshot()["runtime_state"] == "stopped"


_SHARED_IMAGE = "registry.example.com/vllm@sha256:" + "a" * 64
_AUDIO_IMAGE = "registry.example.com/vllm-audio@sha256:" + "b" * 64
_REV_A = "0" * 40
_REV_B = "1" * 40


def _catalog_yaml(*, audio_image: str | None) -> str:
    audio_line = f"\n    image: {audio_image}" if audio_image is not None else ""
    return f"""
version: 1
public_model: local-main
default_profile: base
runtime:
  compose_service: main-llm-vllm
  image: {_SHARED_IMAGE}
profiles:
  base:
    display_name: Base
    model_id: org/base
    revision: "{_REV_A}"
    served_model_name: local-main
    compatibility:
      status: verified
    command: [--model, org/base, --revision, "{_REV_A}", --served-model-name, local-main]
  audio:
    display_name: Audio{audio_line}
    model_id: org/audio
    revision: "{_REV_B}"
    served_model_name: local-main
    compatibility:
      status: verified
    command: [--model, org/audio, --revision, "{_REV_B}", --served-model-name, local-main]
"""


def _write_catalog_path(tmp_path, *, audio_image: str | None = None):
    path = tmp_path / "profiles.yaml"
    path.write_text(_catalog_yaml(audio_image=audio_image), encoding="utf-8")
    return path


def _write_catalog(tmp_path, *, audio_image: str | None, env: dict[str, str] | None = None):
    return load_main_model_catalog(
        _write_catalog_path(tmp_path, audio_image=audio_image), env=env
    )


def test_profile_image_env_ref_resolves_from_env(tmp_path):
    # CI/deploy injects the immutable digest; the profile pins ${AUDIO_VLLM_IMAGE}.
    loaded = _write_catalog(
        tmp_path, audio_image="${AUDIO_VLLM_IMAGE}", env={"AUDIO_VLLM_IMAGE": _AUDIO_IMAGE}
    )
    assert loaded.profiles["audio"].image == _AUDIO_IMAGE
    assert loaded.profiles["base"].image == _SHARED_IMAGE


def test_profile_image_env_ref_falls_back_to_shared_when_unset(tmp_path):
    # No digest built yet -> the env is unset -> the profile inherits the shared base,
    # so the sidecar still boots (the media boot canary, not the pin, gates go-live).
    loaded = _write_catalog(tmp_path, audio_image="${AUDIO_VLLM_IMAGE}", env={})
    assert loaded.profiles["audio"].image == _SHARED_IMAGE


def test_profile_image_env_ref_rejects_non_digest_value(tmp_path):
    with pytest.raises(MainModelConfigurationError):
        _write_catalog(
            tmp_path,
            audio_image="${AUDIO_VLLM_IMAGE}",
            env={"AUDIO_VLLM_IMAGE": "registry.example.com/vllm-audio:latest"},
        )


def test_profile_without_image_inherits_shared_runtime_image(tmp_path):
    loaded = _write_catalog(tmp_path, audio_image=None)
    # Neither profile pins an image -> both resolve to the shared runtime.image,
    # so a profile switch never silently changes the runtime backing 26b.
    assert loaded.profiles["base"].image == _SHARED_IMAGE
    assert loaded.profiles["audio"].image == _SHARED_IMAGE
    assert loaded.profiles["base"].public_view()["runtime_image"] == _SHARED_IMAGE


def test_profile_image_override_travels_with_profile(tmp_path):
    loaded = _write_catalog(tmp_path, audio_image=_AUDIO_IMAGE)
    # The audio profile pins its own runtime; the base profile is untouched.
    assert loaded.profiles["base"].image == _SHARED_IMAGE
    assert loaded.profiles["audio"].image == _AUDIO_IMAGE


def test_profile_image_must_be_digest_pinned(tmp_path):
    with pytest.raises(MainModelConfigurationError):
        _write_catalog(tmp_path, audio_image="registry.example.com/vllm-audio:latest")


def test_snapshot_runtime_image_reflects_active_profile(tmp_path):
    loaded = _write_catalog(tmp_path, audio_image=_AUDIO_IMAGE)
    store = MainModelStateStore(tmp_path / "state.json", loaded.default_profile)
    store.write({**store.read(), "active_profile": "audio", "gate": "open"})
    manager = MainModelManager(loaded, store, FakeBackend("audio"))
    # The live runtime image follows the active profile, not the shared default.
    assert manager.snapshot()["runtime_image"] == _AUDIO_IMAGE


def test_gpu_util_override_rewrites_command_and_fraction():
    from ai_model_serving.main_model_control import load_main_model_catalog as _load

    loaded = _load(ROOT / "configs/main_model_profiles.yaml", gpu_memory_utilization_override=0.55)
    # Override flows to both the runtime command and the parsed budget cost, in
    # lockstep, for every profile (a per-host capacity knob).
    for profile in loaded.profiles.values():
        cmd = list(profile.command)
        assert cmd[cmd.index("--gpu-memory-utilization") + 1] == "0.55"
        assert profile.vram_fraction == 0.55


def test_gpu_util_override_absent_keeps_catalog_value():
    loaded = catalog()
    assert loaded.profiles["gemma4-26b-a4b-fp8"].vram_fraction == 0.76


def test_gpu_util_override_appends_when_command_omits_flag(tmp_path):
    # The base/audio fixtures carry no --gpu-memory-utilization; the override adds it.
    loaded = load_main_model_catalog(
        _write_catalog_path(tmp_path), gpu_memory_utilization_override=0.5
    )
    cmd = list(loaded.profiles["base"].command)
    assert cmd[cmd.index("--gpu-memory-utilization") + 1] == "0.5"
    assert loaded.profiles["base"].vram_fraction == 0.5


def test_gpu_util_override_from_mapping_parses_and_validates():
    from ai_model_serving.main_model_control import gpu_util_override_from_mapping as f

    assert f({}) is None
    assert f({"MAIN_LLM_GPU_MEMORY_UTILIZATION": ""}) is None
    assert f({"MAIN_LLM_GPU_MEMORY_UTILIZATION": "0.9"}) == 0.9
    for bad in ("abc", "0", "-0.1", "1.5"):
        with pytest.raises(MainModelConfigurationError):
            f({"MAIN_LLM_GPU_MEMORY_UTILIZATION": bad})
