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
        "test_release_hygiene_static.py",
        "test_release_package_smoke.py",
    }
)


_MARKER_PATH_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gateway", ("tests/unit/gateway",)),
    ("settings", ("tests/unit/test_settings.py", "tests/unit/test_dotenv_parser.py", "tests/unit/test_setup_env.py")),
    ("auth", ("tests/unit/test_auth_", "tests/contract/exposure",)),
    ("openapi", ("tests/unit/test_openapi_contracts.py", "tests/contract/test_openapi_")),
    ("docs", ("tests/contract/test_document_", "tests/contract/test_korean_documentation_hygiene.py")),
    ("compose", ("tests/contract/governance/test_compose_", "tests/contract/exposure/test_compose_")),
    ("monitoring", ("tests/contract/governance/test_monitoring_", "tests/unit/test_validate_grafana_promql.py")),
    ("package", ("tests/contract/test_release_package_smoke.py",)),
    ("model_registry", ("tests/unit/test_model_registry.py", "tests/unit/test_modelctl.py", "tests/contract/test_render_runtime_assets.py")),
    ("cli", ("tests/contract/test_command_registry.py", "tests/unit/test_auth_plan.py")),
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = Path(str(item.path))
        normalized = path.as_posix()
        parts = path.parts
        if "unit" in parts:
            item.add_marker(pytest.mark.unit)
        if "contract" in parts:
            item.add_marker(pytest.mark.contract)
        if path.name in _GOVERNANCE_FILES or "tests/contract/governance" in normalized or "tests/contract/exposure" in normalized:
            item.add_marker(pytest.mark.governance)
        for marker_name, prefixes in _MARKER_PATH_RULES:
            if any(prefix in normalized for prefix in prefixes):
                item.add_marker(getattr(pytest.mark, marker_name))
