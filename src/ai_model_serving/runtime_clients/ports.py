from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol


class RuntimeEndpointInfo(Protocol):
    logical_id: str
    base_url: str
    model: str


class JsonRuntimeClient(Protocol):
    endpoint: RuntimeEndpointInfo

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        ...


class UpstreamStreamHandle(Protocol):
    """admission을 이미 통과한 upstream SSE 본문."""

    queue_wait_seconds: float

    def __aiter__(self) -> AsyncIterator[bytes]:
        ...

    async def aclose(self) -> None:
        ...


class StreamingRuntimeClient(JsonRuntimeClient, Protocol):
    async def open_stream(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> UpstreamStreamHandle:
        """admission을 먼저 통과시킨 뒤 SSE 본문 handle을 돌려준다.

        admission 거부는 응답 헤더가 정해지기 전에 raise되어야 하므로 이 메서드는
        async generator가 아니라 coroutine이다.
        """
        ...
