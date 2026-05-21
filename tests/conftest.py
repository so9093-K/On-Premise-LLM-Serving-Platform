from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from fastapi import testclient as fastapi_testclient


class InlineASGITestClient:
    """Small TestClient replacement that avoids anyio's background thread portal.

    The sandbox used by automated reviews can block cross-thread event-loop wakeups,
    which makes Starlette's TestClient hang before the app is called. Unit tests in
    this repository only need straightforward ASGI request/response execution, so
    run each request in the current thread through httpx.ASGITransport.
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

_GOVERNANCE_FILES = frozenset(
    {
        "test_exposure_profiles.py",
        "test_governance_and_ops.py",
        "test_release_hygiene_static.py",
        "test_release_package_smoke.py",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = Path(str(item.path))
        parts = path.parts
        if "unit" in parts:
            item.add_marker(pytest.mark.unit)
        if "contract" in parts:
            item.add_marker(pytest.mark.contract)
        if path.name in _GOVERNANCE_FILES:
            item.add_marker(pytest.mark.governance)
