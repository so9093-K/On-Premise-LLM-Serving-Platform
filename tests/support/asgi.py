"""스레드 portal 없이 ASGI 앱을 호출하는 테스트용 HTTP client."""

from __future__ import annotations

from typing import Any

import anyio
import httpx


class InlineASGITestClient:
    """FastAPI ``TestClient``가 동작하지 않는 sandbox용 명시적 대체 client."""

    __test__ = False

    def __init__(self, app: Any, *, raise_server_exceptions: bool = True, **_: Any) -> None:
        self.app = app
        self.raise_server_exceptions = raise_server_exceptions

    def __enter__(self) -> InlineASGITestClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        transport = httpx.ASGITransport(
            app=self.app,
            raise_app_exceptions=self.raise_server_exceptions,
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def run_request() -> httpx.Response:
            return await self._request(method, url, **kwargs)

        return anyio.run(run_request)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)
