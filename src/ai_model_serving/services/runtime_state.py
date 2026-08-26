from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..runtime_topology import load_runtime_topology


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


class RuntimeStateStore:
    """Gateway-side desired-state store for controllable vLLM runtimes.

    State reflects what the gateway *intends* to do with each runtime, not the
    actual container status. When ``path`` is supplied, that desired state is
    persisted so deliberate operator stops survive Gateway restarts.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        controllable_keys: set[str] | frozenset[str] | None = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._path = Path(path) if path is not None else None
        self.controllable_keys = (
            _default_controllable_keys()
            if controllable_keys is None
            else frozenset(controllable_keys)
        )
        now = time.time()
        self._records: dict[str, RuntimeStateRecord] = {
            k: RuntimeStateRecord(RuntimeState.active, source="default", updated_at=now)
            for k in self.controllable_keys
        }
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._records.update(self._read_file())

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

    def _read_file(self) -> dict[str, RuntimeStateRecord]:
        if self._path is None or not self._path.exists():
            return {}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        states = value.get("states") if isinstance(value, dict) else None
        if not isinstance(states, dict):
            return {}
        parsed: dict[str, RuntimeStateRecord] = {}
        for key, raw in states.items():
            if key not in self.controllable_keys:
                continue
            record = self._parse_record(raw)
            if record is not None:
                parsed[key] = record
        return parsed

    def _write_file(self) -> None:
        if self._path is None:
            return
        payload = {
            "schema_version": 2,
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
            if service_key in self.controllable_keys:
                self._records[service_key] = RuntimeStateRecord(
                    state,
                    reason=reason,
                    source=source,
                    updated_at=time.time(),
                )
                self._write_file()

    async def all_records(self) -> dict[str, RuntimeStateRecord]:
        async with self._lock:
            return dict(self._records)
