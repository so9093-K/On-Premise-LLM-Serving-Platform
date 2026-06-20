from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..docker_main_model_backend import DockerMainModelBackend
from ..main_model_control import (
    MainModelManager,
    MainModelStateError,
    MainModelStateStore,
    MainModelSwitchError,
    load_main_model_catalog,
)

# --------------------------------------------------------------------- config

DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT", "")
APP_CONFIG_ROOT = Path(os.environ.get("APP_CONFIG_ROOT", "/app"))
_using_local_config = False
if not (APP_CONFIG_ROOT / "configs/main_model_profiles.yaml").exists():
    APP_CONFIG_ROOT = Path(__file__).resolve().parents[3]
    _using_local_config = True
_default_state_path = (
    APP_CONFIG_ROOT / ".runtime/main-model/main-model-state.json"
    if _using_local_config
    else Path("/var/lib/ai-model-serving/main-model-state.json")
)
MAIN_MODEL_STATE_PATH = Path(os.environ.get("MAIN_MODEL_STATE_PATH", str(_default_state_path)))
MAIN_LLM_BOOT_PROFILE = os.environ.get("MAIN_LLM_BOOT_PROFILE")
MAIN_LLM_PROFILE_LOCKED = os.environ.get("MAIN_LLM_PROFILE_LOCKED", "false").lower() == "true"
SIDECAR_TOKEN = os.environ.get("INTERNAL_SERVICE_TOKEN", "")

# Only these compose service names can be started/stopped.
CONTROLLABLE: frozenset[str] = frozenset({
    "embedding-vllm",
    "embedding-ko-vllm",
    "risk-prompt-vllm",
})

# Container health ports derived from model_serving.yaml (single source of truth).
_serving_cfg_path = APP_CONFIG_ROOT / "configs/model_serving.yaml"
_serving_cfg = yaml.safe_load(_serving_cfg_path.read_text(encoding="utf-8")) if _serving_cfg_path.exists() else {}
_HEALTH_PORT: dict[str, int] = {
    svc["endpoint"].split("//", 1)[1].split(":")[0]: int(svc["port"])
    for svc in _serving_cfg.get("models", {}).values()
    if svc.get("backend") == "vllm"
    and svc.get("endpoint")
    and svc.get("port") is not None
    and svc["endpoint"].split("//", 1)[1].split(":")[0] in CONTROLLABLE
}

# GPU startup order (policy): before starting B, wait for A to be healthy.
# Ports are resolved via _HEALTH_PORT at runtime.
_START_PREREQUISITES: dict[str, list[str]] = {
    "embedding-ko-vllm": ["embedding-vllm"],
    "risk-prompt-vllm": ["embedding-vllm", "embedding-ko-vllm"],
}

# ------------------------------------------------------------------ docker api

def _docker_client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
    return httpx.AsyncClient(transport=transport, base_url="http://docker")


def _label_filter(service: str) -> str:
    labels = [f"com.docker.compose.service={service}"]
    if COMPOSE_PROJECT:
        labels.append(f"com.docker.compose.project={COMPOSE_PROJECT}")
    return json.dumps({"label": labels})


async def _find_container_id(service: str) -> str | None:
    async with _docker_client() as dc:
        resp = await dc.get(
            "/containers/json",
            params={"all": "true", "filters": _label_filter(service)},
            timeout=5.0,
        )
        resp.raise_for_status()
        containers = resp.json()
        return containers[0]["Id"] if containers else None


async def _container_status(service: str) -> str:
    async with _docker_client() as dc:
        resp = await dc.get(
            "/containers/json",
            params={"all": "true", "filters": _label_filter(service)},
            timeout=5.0,
        )
        resp.raise_for_status()
        containers = resp.json()
        if not containers:
            return "not_found"
        return containers[0]["State"]  # "running", "exited", "paused", etc.


async def _do_stop(container_id: str) -> None:
    async with _docker_client() as dc:
        resp = await dc.post(f"/containers/{container_id}/stop", timeout=30.0)
        if resp.status_code not in (204, 304):
            resp.raise_for_status()


async def _do_start(container_id: str) -> None:
    async with _docker_client() as dc:
        resp = await dc.post(f"/containers/{container_id}/start", timeout=30.0)
        if resp.status_code not in (204, 304):
            resp.raise_for_status()


async def _wait_healthy(service: str, port: int, timeout: float = 120.0) -> bool:
    url = f"http://{service}:{port}/health"
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=2.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(3.0)
    return False


_catalog = load_main_model_catalog(APP_CONFIG_ROOT / "configs/main_model_profiles.yaml")
_state_store = MainModelStateStore(MAIN_MODEL_STATE_PATH, _catalog.default_profile)
try:
    _state_store.read()
except MainModelStateError as exc:
    _state_store.quarantine_corrupt_state(str(exc))
_main_model_manager = MainModelManager(
    _catalog,
    _state_store,
    DockerMainModelBackend(
        DOCKER_SOCKET,
        COMPOSE_PROJECT,
        gateway_url=os.environ.get("GATEWAY_INTERNAL_URL", "http://gateway:9400"),
        internal_token=SIDECAR_TOKEN,
    ),
    boot_profile=MAIN_LLM_BOOT_PROFILE,
    profile_locked=MAIN_LLM_PROFILE_LOCKED,
)
_initialization_error: str | None = None
_initialized = asyncio.Event()
# Hold strong references to background tasks so they are not garbage collected
# mid-flight (asyncio only keeps weak references to scheduled tasks).
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


async def _require_sidecar_token(authorization: str | None = Header(default=None)) -> None:
    if not SIDECAR_TOKEN:
        return
    if authorization != f"Bearer {SIDECAR_TOKEN}":
        raise HTTPException(401, detail="invalid internal service token")


async def _run_initialize() -> None:
    global _initialization_error
    try:
        await _main_model_manager.initialize()
    except Exception as exc:  # noqa: BLE001 - surfaced via /health, must not crash the loop
        _initialization_error = str(exc)
    finally:
        _initialized.set()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    del app
    # Main-model reconciliation can block up to startup_timeout_seconds (600s)
    # waiting for the main-llm runtime to become healthy. Awaiting it here would
    # delay uvicorn's startup, leaving /health unreachable past the container's
    # ~40s healthcheck budget on a cold deploy -> the sidecar is marked unhealthy
    # and the Gateway's depends_on(service_healthy) fails the whole rollout.
    # The sidecar is a control plane: its liveness is independent of main-llm
    # boot, which is tracked by the gate (read by the Gateway) instead.
    init_task = asyncio.create_task(_run_initialize())
    _BACKGROUND_TASKS.add(init_task)
    init_task.add_done_callback(_BACKGROUND_TASKS.discard)
    try:
        yield
    finally:
        init_task.cancel()


# --------------------------------------------------------------------- app

app = FastAPI(
    title="Admin Sidecar",
    description="Internal container lifecycle control. Not exposed publicly.",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=_lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    # Control-plane liveness is intentionally decoupled from main-llm boot.
    # While reconciliation is still in progress the sidecar is healthy and the
    # gate stays closed (the Gateway fails closed on local-main). Only a
    # *definitive* reconciliation failure is surfaced as unhealthy.
    if _initialized.is_set() and _initialization_error:
        raise HTTPException(503, detail=f"main model reconciliation failed: {_initialization_error}")
    return {"status": "ok"}


@app.get("/containers/status")
async def containers_status(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_sidecar_token(authorization)
    statuses: dict[str, str] = {}
    for service in CONTROLLABLE:
        try:
            statuses[service] = await _container_status(service)
        except Exception as exc:
            statuses[service] = f"error: {exc}"
    return JSONResponse({"containers": statuses})


@app.get("/main-model")
async def main_model(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_sidecar_token(authorization)
    try:
        # jsonable_encoder coerces date/datetime (and other non-JSON-native types)
        # to serializable forms. Without it a single date value in the profile
        # catalog (e.g. an unquoted validated_at) makes this endpoint 500, which the
        # Gateway reads as SidecarUnavailable and fails every main-model request.
        return JSONResponse(jsonable_encoder(_main_model_manager.snapshot()))
    except MainModelStateError as exc:
        raise HTTPException(503, detail=str(exc)) from exc


@app.get("/main-model/profiles")
async def main_model_profiles(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_sidecar_token(authorization)
    return JSONResponse(jsonable_encoder({"profiles": _main_model_manager.profiles()}))


@app.post("/main-model/switch", status_code=202)
async def switch_main_model(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    await _require_sidecar_token(authorization)
    unknown = set(payload) - {"profile", "confirm_unverified", "request_id"}
    if unknown:
        raise HTTPException(422, detail=f"unsupported fields: {sorted(unknown)}")
    try:
        operation_id = _main_model_manager.request_switch(
            str(payload.get("profile", "")),
            confirm_unverified=payload.get("confirm_unverified") is True,
            client_request_id=payload.get("request_id"),
        )
    except MainModelSwitchError as exc:
        raise HTTPException(
            exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return JSONResponse({"operation_id": operation_id, "status": "pending"}, status_code=202)


@app.post("/main-model/rollback", status_code=202)
async def rollback_main_model(
    payload: dict[str, Any] | None = None,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    await _require_sidecar_token(authorization)
    body = payload or {}
    unknown = set(body) - {"request_id"}
    if unknown:
        raise HTTPException(422, detail=f"unsupported fields: {sorted(unknown)}")
    try:
        operation_id = _main_model_manager.request_rollback(
            client_request_id=body.get("request_id")
        )
    except MainModelSwitchError as exc:
        raise HTTPException(
            exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return JSONResponse({"operation_id": operation_id, "status": "pending"}, status_code=202)


@app.get("/main-model/operations/{operation_id}")
async def main_model_operation(
    operation_id: str,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    await _require_sidecar_token(authorization)
    operation = _main_model_manager.operation(operation_id)
    if operation is None:
        raise HTTPException(404, detail="operation not found")
    return JSONResponse(operation)


@app.post("/containers/{service}/stop")
async def stop_container(
    service: str,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    await _require_sidecar_token(authorization)
    if service not in CONTROLLABLE:
        raise HTTPException(403, detail=f"not controllable: {service}")
    container_id = await _find_container_id(service)
    if container_id is None:
        raise HTTPException(404, detail=f"container not found: {service}")
    await _do_stop(container_id)
    return JSONResponse({"action": "stop", "service": service, "stopped": [service]})


@app.post("/containers/{service}/start")
async def start_container(
    service: str,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    await _require_sidecar_token(authorization)
    if service not in CONTROLLABLE:
        raise HTTPException(403, detail=f"not controllable: {service}")

    started: list[str] = []

    # Start prerequisites in order (serial GPU memory profiling)
    for prereq in _START_PREREQUISITES.get(service, []):
        prereq_port = _HEALTH_PORT[prereq]
        status = await _container_status(prereq)
        if status == "running":
            continue
        prereq_id = await _find_container_id(prereq)
        if prereq_id is None:
            raise HTTPException(503, detail=f"prerequisite container not found: {prereq}")
        await _do_start(prereq_id)
        if not await _wait_healthy(prereq, prereq_port):
            raise HTTPException(
                503, detail=f"prerequisite {prereq} did not become healthy within timeout"
            )
        started.append(prereq)

    container_id = await _find_container_id(service)
    if container_id is None:
        raise HTTPException(404, detail=f"container not found: {service}")
    await _do_start(container_id)
    started.append(service)

    return JSONResponse({"action": "start", "service": service, "started": started})
