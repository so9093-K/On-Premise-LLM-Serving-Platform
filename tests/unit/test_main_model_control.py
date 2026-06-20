from __future__ import annotations

import asyncio
import json
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
    ) -> None:
        self.observed = observed
        self.fail_profile = fail_profile
        self.fail_drain = fail_drain
        self.fail_prepare = fail_prepare
        self.prepared: list[str] = []
        self.replaced: list[str] = []
        self.drain_calls = 0

    async def observed_profile(self, catalog):
        return self.observed

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
        if profile.profile_id == self.fail_profile:
            raise RuntimeError("validation failed")


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
        operation_id = manager.request_switch(
            "gemma4-12b-unified-fp8", confirm_unverified=True
        )
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
        operation_id = manager.request_switch(
            "gemma4-12b-unified-fp8", confirm_unverified=True
        )
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
        operation_id = manager.request_switch(
            "gemma4-12b-unified-fp8", confirm_unverified=True
        )
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
        operation_id = manager.request_switch(
            "gemma4-12b-unified-fp8", confirm_unverified=True
        )
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
        operation_id = manager.request_switch(
            "gemma4-12b-unified-fp8", confirm_unverified=True
        )
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
            confirm_unverified=True,
            client_request_id="deploy-12b-1",
        )
        second = manager.request_switch(
            "gemma4-12b-unified-fp8",
            confirm_unverified=True,
            client_request_id="deploy-12b-1",
        )
        assert first == second
        while manager.operation(first)["status"] not in {
            "completed",
            "failed",
            "rollback_failed",
        }:
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert len(store.read()["operations"]) == 1


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
