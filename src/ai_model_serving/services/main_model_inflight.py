from __future__ import annotations

import asyncio
from types import TracebackType


class MainModelInFlight:
    """현재 process가 수락한 main-model 요청 수를 추적한다.

    The sidecar closes the persisted request gate before polling this count.
    With the supported single-worker Gateway deployment, reaching zero means
    all requests accepted before the gate closed have completed or disconnected.
    """

    def __init__(self) -> None:
        self._count = 0
        self._lock = asyncio.Lock()

    def track(self) -> "_InFlightScope":
        return _InFlightScope(self)

    async def count(self) -> int:
        async with self._lock:
            return self._count

    async def _acquire(self) -> None:
        async with self._lock:
            self._count += 1

    async def _release(self) -> None:
        async with self._lock:
            self._count -= 1


class _InFlightScope:
    """in-flight 한 건의 수명을 나타내는 async context manager다.

    ``@asynccontextmanager``가 아니라 명시적인 클래스인 이유: generator 기반
    context manager는 종료 시 예외를 generator 안으로 되던지면서 파이썬 레벨에서
    ``exc.__traceback__``을 대입하는데, 이 프로젝트의 ``ServiceError``는 frozen
    dataclass라 그 대입이 ``FrozenInstanceError``로 터진다. 그러면 원래 오류가
    전혀 무관한 예외에 가려진다. ``__aexit__``는 예외를 받기만 하고 되던지지
    않으므로 그 경로가 아예 생기지 않는다.
    """

    __slots__ = ("_owner",)

    def __init__(self, owner: MainModelInFlight) -> None:
        self._owner = owner

    async def __aenter__(self) -> None:
        await self._owner._acquire()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._owner._release()
