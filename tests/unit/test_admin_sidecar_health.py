"""admin-sidecar control-plane healthcheck에 대한 회귀 가드.

예전에 sidecar가 lifespan startup 안에서 main-model reconciliation을 기다리다가
(startup_timeout_seconds까지, 600초) 콜드 배포가 롤백된 적이 있다. uvicorn은
startup이 끝나기 전엔 요청을 받지 않으므로, /health가 컨테이너의 ~40초
healthcheck 예산을 넘도록 응답 불가 상태였고, sidecar는 unhealthy로 표시되어
Gateway의 depends_on(service_healthy)가 전체 롤아웃을 실패시켰다. sidecar는
control plane이다: 그 liveness는 main-llm 부팅(gate가 따로 추적한다)과
독립적이어야 한다.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi import HTTPException
from tests.support.asgi import InlineASGITestClient as TestClient


def _load_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIN_MODEL_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "")
    import ai_model_serving.apps.admin_sidecar as sidecar

    return importlib.reload(sidecar)


def test_lifespan_does_not_block_health_on_main_model_reconciliation(tmp_path, monkeypatch):
    sidecar = _load_sidecar(tmp_path, monkeypatch)

    async def slow_initialize() -> None:
        # validate()가 컨테이너의 healthcheck 예산을 훨씬 넘겨 main-llm health를
        # 기다리는 상황을 시뮬레이션한다. startup이 이걸 await했다면 TestClient의
        # __enter__(lifespan startup을 실행함)가 블록되어 테스트가 멈췄을 것이다.
        await asyncio.sleep(3600)

    monkeypatch.setattr(sidecar._main_model_manager, "initialize", slow_initialize)

    with TestClient(sidecar.app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        # reconciliation은 아직 진행 중이다: 완료도 에러도 아니다.
        assert not sidecar._initialized.is_set()


def test_health_surfaces_definitive_reconciliation_failure(tmp_path, monkeypatch):
    sidecar = _load_sidecar(tmp_path, monkeypatch)

    async def failing_initialize() -> None:
        raise RuntimeError("main runtime did not become healthy")

    monkeypatch.setattr(sidecar._main_model_manager, "initialize", failing_initialize)

    # reconciliation이 끝나지 않은 동안에는 sidecar가 healthy 상태를 유지한다.
    assert asyncio.run(sidecar.health()) == {"status": "ok"}

    # 백그라운드 initializer는 실패를 기록하되 task 밖으로 예외를 던지지 않는다
    # (그리고 완료 이벤트는 항상 set한다).
    asyncio.run(sidecar._run_initialize())
    assert sidecar._initialized.is_set()
    assert "main runtime did not become healthy" in (sidecar._initialization_error or "")

    # 이제 확정된 실패는 unhealthy로 드러난다.
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(sidecar.health())
    assert excinfo.value.status_code == 503
    assert "main runtime did not become healthy" in str(excinfo.value.detail)
