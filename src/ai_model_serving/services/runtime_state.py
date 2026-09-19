from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..runtime_topology import load_runtime_topology
from ..service_logging import service_logger


class RuntimeStateStoreError(RuntimeError):
    pass


class RuntimeState(str, Enum):
    active = "active"
    stopped = "stopped"
    starting = "starting"


@dataclass(frozen=True)
class RuntimeStateRecord:
    state: RuntimeState
    reason: str = ""
    source: str = ""
    updated_at: float = 0.0


def _default_controllable_keys() -> frozenset[str]:
    root = Path(os.environ.get("APP_CONFIG_ROOT", Path(__file__).resolve().parents[3]))
    return load_runtime_topology(root).controllable_keys


_RUNTIME_STATE_KEY_RENAMES = {
    "risk_prompt": "prompt_injection_detector",
}


class RuntimeStateStore:
    """Gateway-side desired-state store for controllable vLLM runtimes.

    State reflects what the gateway *intends* to do with each runtime, not the
    actual container status. When ``path`` is supplied, that desired state is
    persisted so deliberate operator stops survive Gateway restarts.

    A corrupt persistent file must never silently turn an intentional ``stopped``
    state back into the default ``active`` state. Corrupt input is quarantined and
    replaced with an explicit fail-closed recovery state where every controllable
    secondary runtime is ``stopped`` until an operator starts it again.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        controllable_keys: set[str] | frozenset[str] | None = None,
        deferred_keys: Iterable[str] = (),
        activated_keys: Iterable[str] = (),
        release_id: str = "",
    ) -> None:
        self._lock = asyncio.Lock()
        self._path = Path(path) if path is not None else None
        self.controllable_keys = (
            _default_controllable_keys()
            if controllable_keys is None
            else frozenset(controllable_keys)
        )
        self.recovery_error = ""
        self.recovery_quarantine_path: Path | None = None
        now = time.time()
        self._records: dict[str, RuntimeStateRecord] = {
            k: RuntimeStateRecord(RuntimeState.active, source="default", updated_at=now)
            for k in self.controllable_keys
        }
        self._applied_release_id = ""
        had_persisted_state = False
        recovered_corrupt_state = False
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            migration_required = False
            try:
                records, self._applied_release_id, migration_required = self._read_file()
            except RuntimeStateStoreError as exc:
                recovered_corrupt_state = True
                self.recovery_error = str(exc)
                self.recovery_quarantine_path = self._quarantine_corrupt_state()
                recovery_time = time.time()
                self._records = {
                    key: RuntimeStateRecord(
                        RuntimeState.stopped,
                        reason="state_recovery_required",
                        source="recovery",
                        updated_at=recovery_time,
                    )
                    for key in self.controllable_keys
                }
                self._applied_release_id = ""
                try:
                    self._write_file()
                except OSError as write_exc:
                    raise RuntimeStateStoreError(
                        "runtime desired state is corrupt and safe recovery state could not be persisted"
                    ) from write_exc
                service_logger("gateway").error(
                    "runtime desired state was corrupt; quarantined %s and initialized fail-closed state: %s",
                    self.recovery_quarantine_path,
                    exc,
                )
                records = {}
            self._records.update(records)
            had_persisted_state = bool(records)
            if migration_required and not recovered_corrupt_state:
                try:
                    self._write_file()
                except OSError as exc:
                    raise RuntimeStateStoreError(
                        "runtime desired state key migration could not be persisted"
                    ) from exc
        # A corrupt desired-state file means operator intent is unknown. Applying a
        # deploy directive in the same startup could immediately turn a fail-closed
        # recovery state back to active, so recovery always wins for this boot.
        if not recovered_corrupt_state:
            self._apply_deploy_directive(
                deferred_keys,
                activated_keys,
                release_id,
                had_persisted_state=had_persisted_state,
            )

    def _apply_deploy_directive(
        self,
        deferred_keys: Iterable[str],
        activated_keys: Iterable[str],
        release_id: str,
        *,
        had_persisted_state: bool,
    ) -> None:
        """배포가 지정한 deferred runtime을 기동 시 한 번만 stopped로 새긴다.

        배포 스크립트는 이 파일을 직접 쓰지 않는다. Gateway 컨테이너가
        non-root appuser로 쓰는 디렉터리를 배포 사용자가 함께 쓰면, 먼저 만든
        쪽이 소유권을 가져가 반대쪽이 영구히 쓰지 못한다 -- 실제로 그 상태에서
        배포가 조용히 실패한 적이 있다. 그래서 desired state의 writer는
        Gateway 하나로 두고, 배포는 지시만 env로 넘긴다.

        릴리스 ID로 게이팅하는 이유는 재적용을 막기 위해서다. 컨테이너가 단순
        재시작될 때마다 지시를 다시 적용하면, 운영자가 Admin API로 켜 둔 런타임이
        재시작 한 번에 다시 꺼진다. 같은 릴리스에서는 파일에 남은 상태가 이긴다.
        """
        stop_keys = [k for k in deferred_keys if k in self.controllable_keys]
        start_keys = [
            k for k in activated_keys if k in self.controllable_keys and k not in stop_keys
        ]
        if not stop_keys and not start_keys:
            return
        if release_id:
            if release_id == self._applied_release_id:
                return
        elif had_persisted_state:
            # 릴리스 ID 없이 기동한 경우(배포 스크립트를 거치지 않은 수동 실행)
            # 재적용 여부를 판단할 근거가 없다. 남은 desired state가 있으면 그쪽을
            # 신뢰하고, 없을 때만 지시를 적용한다 -- 지시를 조용히 버리지도,
            # 운영자 조작을 매 재시작마다 되돌리지도 않는다.
            return
        now = time.time()
        for key in stop_keys:
            self._records[key] = RuntimeStateRecord(
                RuntimeState.stopped,
                reason="deferred_at_deploy",
                source="deploy",
                updated_at=now,
            )
        # 롤백이 되살린 런타임을 desired state에서도 되돌린다.
        for key in start_keys:
            self._records[key] = RuntimeStateRecord(
                RuntimeState.active,
                reason="restored_at_rollback",
                source="deploy",
                updated_at=now,
            )
        self._applied_release_id = release_id
        try:
            self._write_file()
        except OSError as exc:
            # 배포 지시는 release 단위로 한 번만 적용되어야 한다. 메모리만 바뀐 채
            # 계속 기동하면 다음 restart에서 operator intent가 뒤집힐 수 있으므로
            # persistent writer가 준비되지 않은 운영 배포는 소리내서 실패한다.
            raise RuntimeStateStoreError(
                "failed to persist runtime desired state deploy directive"
            ) from exc

    @staticmethod
    def _parse_record(raw: Any) -> RuntimeStateRecord | None:
        if isinstance(raw, str):
            try:
                return RuntimeStateRecord(RuntimeState(raw))
            except ValueError:
                return None
        if not isinstance(raw, dict):
            return None
        try:
            state = RuntimeState(raw.get("state"))
        except ValueError:
            return None
        try:
            updated_at = float(raw.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            updated_at = 0.0
        return RuntimeStateRecord(
            state=state,
            reason=str(raw.get("reason") or ""),
            source=str(raw.get("source") or ""),
            updated_at=updated_at,
        )

    def _read_file(self) -> tuple[dict[str, RuntimeStateRecord], str, bool]:
        if self._path is None or not self._path.exists():
            return {}, "", False
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except PermissionError as exc:
            raise RuntimeStateStoreError("runtime desired state is not readable") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeStateStoreError("runtime desired state is corrupt") from exc
        if not isinstance(value, dict):
            raise RuntimeStateStoreError("runtime desired state root must be an object")
        schema_version = value.get("schema_version", 1)
        if isinstance(schema_version, bool) or schema_version not in {1, 2}:
            raise RuntimeStateStoreError(
                f"unsupported runtime desired state schema version: {schema_version!r}"
            )
        applied = str(value.get("applied_release_id") or "")
        states = value.get("states")
        if not isinstance(states, dict):
            raise RuntimeStateStoreError("runtime desired state states must be an object")
        parsed: dict[str, RuntimeStateRecord] = {}
        migrated = False
        for key, raw in states.items():
            canonical_key = _RUNTIME_STATE_KEY_RENAMES.get(key, key)
            if canonical_key not in self.controllable_keys:
                if key not in self.controllable_keys:
                    continue
                canonical_key = key
            record = self._parse_record(raw)
            if record is None:
                raise RuntimeStateStoreError(
                    f"runtime desired state contains an invalid record for {key}"
                )
            existing = parsed.get(canonical_key)
            if existing is not None:
                if existing.state != record.state:
                    raise RuntimeStateStoreError(
                        "runtime desired state contains conflicting records for "
                        f"{key} and {canonical_key}"
                    )
                if key == canonical_key:
                    parsed[canonical_key] = record
            else:
                parsed[canonical_key] = record
            migrated = migrated or canonical_key != key
        return parsed, applied, migrated

    def _quarantine_corrupt_state(self) -> Path | None:
        if self._path is None or not self._path.exists():
            return None
        target = self._path.with_name(f"{self._path.name}.corrupt.{time.time_ns()}")
        os.replace(self._path, target)
        self._fsync_directory()
        return target

    def _fsync_directory(self) -> None:
        if self._path is None:
            return
        directory_fd = os.open(self._path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _write_file(self) -> None:
        if self._path is None:
            return
        payload = {
            "schema_version": 2,
            # 배포 지시를 어느 릴리스에서 적용했는지 함께 남긴다. 같은 릴리스로
            # 컨테이너가 재시작되면 지시를 다시 적용하지 않는 근거가 된다.
            "applied_release_id": self._applied_release_id,
            "states": {
                key: {
                    "state": record.state.value,
                    "reason": record.reason,
                    "source": record.source,
                    "updated_at": record.updated_at,
                }
                for key, record in sorted(self._records.items())
            },
        }
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o644)
            os.replace(temp_name, self._path)
            self._fsync_directory()
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    async def get(self, service_key: str) -> RuntimeState:
        async with self._lock:
            return self._records.get(
                service_key,
                RuntimeStateRecord(RuntimeState.active),
            ).state

    async def set(
        self,
        service_key: str,
        state: RuntimeState,
        *,
        reason: str = "",
        source: str = "",
    ) -> None:
        async with self._lock:
            if service_key not in self.controllable_keys:
                return
            previous = self._records.get(service_key)
            self._records[service_key] = RuntimeStateRecord(
                state,
                reason=reason,
                source=source,
                updated_at=time.time(),
            )
            try:
                self._write_file()
            except OSError:
                if previous is None:
                    self._records.pop(service_key, None)
                else:
                    self._records[service_key] = previous
                raise

    async def all_records(self) -> dict[str, RuntimeStateRecord]:
        async with self._lock:
            return dict(self._records)
