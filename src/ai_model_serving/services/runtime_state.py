from __future__ import annotations

import asyncio
from enum import Enum


class RuntimeState(str, Enum):
    active = "active"
    disabled = "disabled"
    stopped = "stopped"
    starting = "starting"


CONTROLLABLE_KEYS = frozenset({"embedding", "embedding_ko", "risk_prompt"})


class RuntimeStateStore:
    """In-memory gateway-side state for controllable vLLM runtimes.

    State reflects what the gateway *intends* to do with each runtime, not the
    actual container status.  The admin control router reconciles both views on
    every /admin/runtimes GET.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._states: dict[str, RuntimeState] = {
            k: RuntimeState.active for k in CONTROLLABLE_KEYS
        }

    async def get(self, service_key: str) -> RuntimeState:
        async with self._lock:
            return self._states.get(service_key, RuntimeState.active)

    async def set(self, service_key: str, state: RuntimeState) -> None:
        async with self._lock:
            if service_key in CONTROLLABLE_KEYS:
                self._states[service_key] = state

    async def sync(self, states: dict[str, RuntimeState]) -> None:
        """Bulk-update states, e.g. after sidecar start/stop returns."""
        async with self._lock:
            for key, state in states.items():
                if key in CONTROLLABLE_KEYS:
                    self._states[key] = state

    async def all(self) -> dict[str, RuntimeState]:
        async with self._lock:
            return dict(self._states)
