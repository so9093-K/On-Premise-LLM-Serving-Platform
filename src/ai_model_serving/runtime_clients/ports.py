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


class StreamingRuntimeClient(JsonRuntimeClient, Protocol):
    def stream_bytes(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[bytes]:
        ...
