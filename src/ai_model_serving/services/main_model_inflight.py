from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class MainModelInFlight:
    """현재 process가 수락한 main-model 요청 수를 추적한다.

    The sidecar closes the persisted request gate before polling this count.
    With the supported single-worker Gateway deployment, reaching zero means
    all requests accepted before the gate closed have completed or disconnected.
    """

    def __init__(self) -> None:
        self._count = 0
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def track(self) -> AsyncIterator[None]:
        async with self._lock:
            self._count += 1
        try:
            yield
        finally:
            async with self._lock:
                self._count -= 1

    async def count(self) -> int:
        async with self._lock:
            return self._count
