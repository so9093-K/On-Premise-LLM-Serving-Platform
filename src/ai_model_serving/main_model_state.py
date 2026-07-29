from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class MainModelStateError(RuntimeError):
    pass


class MainModelSwitchLockError(RuntimeError):
    pass


def read_main_model_state_file(path: Path) -> dict[str, Any] | None:
    """상태 파일을 변경하지 않고 읽어 공통 스키마만 검증한다."""
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except PermissionError as exc:
        raise MainModelStateError(
            f"main model state is not readable (permission denied): {path}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise MainModelStateError(f"main model state is corrupt: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise MainModelStateError("unsupported main model state schema")
    return value


def read_active_profile(path: Path) -> str | None:
    """부팅 시 사용할 마지막 활성 프로필을 상태 저장소와 같은 규칙으로 읽는다."""
    state = read_main_model_state_file(path)
    if state is None:
        return None
    active = state.get("active_profile")
    if active is not None and not isinstance(active, str):
        raise MainModelStateError("active_profile must be a string or null")
    return active


class MainModelStateStore:
    """프로세스 안전 파일 잠금으로 main-model 제어 상태를 저장한다."""

    def __init__(self, path: Path, default_profile: str) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.operation_lock_path = path.with_suffix(path.suffix + ".operation.lock")
        self.default_profile = default_profile
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _initial(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "active_profile": None,
            "last_known_good_profile": None,
            "previous_known_good_profile": None,
            "gate": "closed",
            "runtime_state": "active",
            "last_operation": None,
            "last_validated_container_started_at": None,
            "operations": [],
            "stats": {
                "switch_requests": 0,
                "switch_successes": 0,
                "switch_failures": 0,
                "rollbacks": 0,
                "rollback_failures": 0,
                "last_switch_timestamp": 0,
                "last_switch_duration_seconds": 0,
            },
            "state_recovery_error": None,
        }

    def _read_unlocked(self) -> dict[str, Any]:
        state = read_main_model_state_file(self.path)
        if state is None:
            return self._initial()
        return state

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o644)
            os.replace(temp_name, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def read(self) -> dict[str, Any]:
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            return self._read_unlocked()

    def write(self, state: dict[str, Any]) -> None:
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self._write_unlocked(state)

    def update(self, mutate: Any) -> dict[str, Any]:
        """하나의 프로세스 잠금 안에서 상태를 원자적으로 읽고 변경·교체한다."""
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._read_unlocked()
            mutate(state)
            self._write_unlocked(state)
            return state

    def quarantine_corrupt_state(self, reason: str) -> Path | None:
        """읽을 수 없는 상태 파일을 격리하고 fail-closed 초기 상태를 만든다."""
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            quarantined: Path | None = None
            if self.path.exists():
                quarantined = self.path.with_name(f"{self.path.name}.corrupt.{int(time.time())}")
                os.replace(self.path, quarantined)
            state = self._initial()
            state["state_recovery_error"] = reason
            self._write_unlocked(state)
            return quarantined

    @contextmanager
    def operation_lock(self) -> Iterator[None]:
        """전체 비동기 전환 작업 동안 전역 switch 잠금을 유지한다."""
        self.operation_lock_path.touch(exist_ok=True)
        handle = self.operation_lock_path.open("r+", encoding="utf-8")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise MainModelSwitchLockError("another model switch process holds the lock") from exc
            yield
        finally:
            handle.close()
