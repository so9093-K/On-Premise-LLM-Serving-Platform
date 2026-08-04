"""전역 pytest conftest. 샌드박스에서 anyio 백그라운드 스레드가 멈추는 문제를
피하려고 FastAPI TestClient를 InlineASGITestClient로 교체한다."""

from __future__ import annotations

from typing import Any

import anyio
import httpx
from fastapi import testclient as fastapi_testclient


class InlineASGITestClient:
    """anyio의 백그라운드 스레드 portal을 피하는 작은 TestClient 대체품.

    자동 리뷰에 쓰이는 샌드박스는 스레드 간 event-loop wakeup을 막을 수 있어서,
    Starlette의 TestClient가 앱 호출 전에 멈춰버릴 수 있다. 이 저장소의 단위
    테스트는 단순한 ASGI request/response 실행만 필요하므로, httpx.ASGITransport를
    통해 각 요청을 현재 스레드에서 실행한다.
    """

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


fastapi_testclient.TestClient = InlineASGITestClient
