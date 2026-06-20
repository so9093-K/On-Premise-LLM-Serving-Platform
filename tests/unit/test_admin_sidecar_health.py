"""Regression guards for the admin-sidecar control-plane healthcheck.

A cold deploy once rolled back because the sidecar awaited main-model
reconciliation (up to startup_timeout_seconds, 600s) inside its lifespan
startup. uvicorn does not accept requests until startup finishes, so /health
stayed unreachable past the container's ~40s healthcheck budget, the sidecar was
marked unhealthy, and the Gateway's depends_on(service_healthy) failed the whole
rollout. The sidecar is a control plane: its liveness must be independent of
main-llm boot (which is tracked by the gate instead).
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def _load_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIN_MODEL_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "")
    import ai_model_serving.apps.admin_sidecar as sidecar

    return importlib.reload(sidecar)


def test_lifespan_does_not_block_health_on_main_model_reconciliation(tmp_path, monkeypatch):
    sidecar = _load_sidecar(tmp_path, monkeypatch)

    async def slow_initialize() -> None:
        # Simulate validate() waiting on main-llm health far beyond the
        # container's healthcheck budget. If startup awaited this, TestClient's
        # __enter__ (which runs lifespan startup) would block and hang the test.
        await asyncio.sleep(3600)

    monkeypatch.setattr(sidecar._main_model_manager, "initialize", slow_initialize)

    with TestClient(sidecar.app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        # Reconciliation is still in flight: not completed, not errored.
        assert not sidecar._initialized.is_set()


def test_health_surfaces_definitive_reconciliation_failure(tmp_path, monkeypatch):
    sidecar = _load_sidecar(tmp_path, monkeypatch)

    async def failing_initialize() -> None:
        raise RuntimeError("main runtime did not become healthy")

    monkeypatch.setattr(sidecar._main_model_manager, "initialize", failing_initialize)

    # While reconciliation has not finished, the sidecar stays healthy.
    assert asyncio.run(sidecar.health()) == {"status": "ok"}

    # The background initializer records the failure without raising out of the
    # task (and always sets the completion event).
    asyncio.run(sidecar._run_initialize())
    assert sidecar._initialized.is_set()
    assert "main runtime did not become healthy" in (sidecar._initialization_error or "")

    # A definitive failure is now surfaced as unhealthy.
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(sidecar.health())
    assert excinfo.value.status_code == 503
    assert "main runtime did not become healthy" in str(excinfo.value.detail)
