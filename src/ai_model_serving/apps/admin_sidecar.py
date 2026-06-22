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
from ..gpu_budget import Participant, budget_snapshot, plan_activation
from ..main_model_control import (
    MainModelManager,
    MainModelStateError,
    MainModelStateStore,
    MainModelSwitchError,
    gpu_util_override_from_mapping,
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

# Only these compose service names can be started/stopped. Kept in sync with
# configs/model_serving.yaml (vLLM models minus the main runtime) and the gateway
# CONTROLLABLE_KEYS by tests/unit/test_runtime_topology_consistency.py.
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
# Ports are resolved via _HEALTH_PORT at runtime. This mirrors the compose
# depends_on graph's secondary<->secondary edges (the main-llm root edge is
# omitted on purpose — see below); kept in sync with compose by
# tests/unit/test_runtime_topology_consistency.py.
_START_PREREQUISITES: dict[str, list[str]] = {
    "embedding-ko-vllm": ["embedding-vllm"],
    "risk-prompt-vllm": ["embedding-vllm", "embedding-ko-vllm"],
}

# Per-secondary VRAM reservation (gpu_memory_utilization) from model_serving.yaml,
# keyed by compose service name -- the cost side of the shared GPU budget.
_VRAM_FRACTION: dict[str, float] = {
    svc["endpoint"].split("//", 1)[1].split(":")[0]: float(svc["gpu_memory_utilization"])
    for svc in _serving_cfg.get("models", {}).values()
    if svc.get("backend") == "vllm"
    and svc.get("endpoint")
    and svc.get("gpu_memory_utilization") is not None
    and svc["endpoint"].split("//", 1)[1].split(":")[0] in CONTROLLABLE
}
# Canonical GPU VRAM budget lives in configs/gpu_budgets.yaml (single source of
# truth, also consumed by modelctl/runtime_validation). The admission ceiling is
# its policy "avoid_above" fraction.
_gpu_budgets_path = APP_CONFIG_ROOT / "configs/gpu_budgets.yaml"
_gpu_budgets_cfg = (
    yaml.safe_load(_gpu_budgets_path.read_text(encoding="utf-8"))
    if _gpu_budgets_path.exists()
    else {}
)
_GPU_BUDGET_CEILING = float(
    ((_gpu_budgets_cfg.get("gpu") or {}).get("total_gpu_memory_utilization") or {}).get(
        "avoid_above", 0.95
    )
)

# Per-secondary criticality (resource_control.criticality) from model_serving.yaml,
# keyed by compose service name. Drives eviction order so the right thing is kept.
_CRITICALITY: dict[str, str] = {
    svc["endpoint"].split("//", 1)[1].split(":")[0]: str(
        (svc.get("resource_control") or {}).get("criticality", "")
    )
    for svc in _serving_cfg.get("models", {}).values()
    if svc.get("backend") == "vllm"
    and svc.get("endpoint")
    and svc["endpoint"].split("//", 1)[1].split(":")[0] in CONTROLLABLE
}
# Eviction policy by criticality: higher priority is evicted LATER. The primary
# user path (main) is never auto-evicted; the risk/safety path is kept longer than
# retrieval support. Unknown roles fall in the default evictable tier.
_CRITICALITY_PRIORITY: dict[str, tuple[int, bool]] = {
    "primary_user_path": (100, False),
    "risk_signal_path": (60, True),
    "retrieval_support_path": (50, True),
}
_MAIN_PRIORITY = 100


def _eviction_policy(criticality: str) -> tuple[int, bool]:
    return _CRITICALITY_PRIORITY.get(criticality, (50, True))

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


_catalog = load_main_model_catalog(
    APP_CONFIG_ROOT / "configs/main_model_profiles.yaml",
    gpu_memory_utilization_override=gpu_util_override_from_mapping(os.environ),
)
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
_MAIN_SERVICE = str(_catalog.runtime["compose_service"])
_initialization_error: str | None = None
_initialized = asyncio.Event()
# Hold strong references to background tasks so they are not garbage collected
# mid-flight (asyncio only keeps weak references to scheduled tasks).
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
# Serializes GPU-budget admission decisions so two activations cannot both pass.
_budget_lock = asyncio.Lock()


async def _build_participants() -> list[Participant]:
    """Current GPU-budget participants: secondaries + the main model."""
    participants: list[Participant] = []
    for service, fraction in _VRAM_FRACTION.items():
        status = await _container_status(service)
        criticality = _CRITICALITY.get(service, "")
        priority, evictable = _eviction_policy(criticality)
        participants.append(
            Participant(
                key=service,
                vram_fraction=fraction,
                active=(status == "running"),
                priority=priority,
                evictable=evictable,
                criticality=criticality or None,
            )
        )
    snapshot = _main_model_manager.snapshot()
    active_profile = snapshot.get("active_profile") or {}
    # Fall back to the boot profile's reservation (not a generic 0.9) when no active
    # profile is recorded yet, so the ledger reflects the real main-model cost.
    _boot_fraction = _catalog.profiles[_main_model_manager.boot_profile].vram_fraction
    main_fraction = float(active_profile.get("vram_fraction") or _boot_fraction)
    main_status = await _container_status(_MAIN_SERVICE)
    participants.append(
        Participant(
            key=_MAIN_SERVICE,
            vram_fraction=main_fraction,
            active=(main_status == "running"),
            priority=_MAIN_PRIORITY,
            evictable=False,
            criticality="primary_user_path",
        )
    )
    return participants


async def _admit_or_raise(target_key: str, target_fraction: float, *, force: bool) -> list[str]:
    """Admit an activation against the shared GPU budget.

    Returns the list of victims stopped (empty if it already fit). Raises 409 with
    a plan when it does not fit (and force is False) or is impossible.
    """
    participants = await _build_participants()
    result = plan_activation(
        participants, target_key, target_fraction, ceiling=_GPU_BUDGET_CEILING
    )
    if result.already_fits:
        return []
    if not result.feasible:
        raise HTTPException(
            409,
            detail={
                "code": "GPU_BUDGET_EXCEEDED",
                "feasible": False,
                "required": round(result.required, 4),
                "available": round(result.available, 4),
                "ceiling": result.ceiling,
                "reason": result.reason,
            },
        )
    if not force:
        raise HTTPException(
            409,
            detail={
                "code": "GPU_BUDGET_EXCEEDED",
                "feasible": True,
                "required": round(result.required, 4),
                "available": round(result.available, 4),
                "ceiling": result.ceiling,
                "plan": {"stop": list(result.victims)},
            },
        )
    stopped: list[str] = []
    for victim in result.victims:
        container_id = await _find_container_id(victim)
        if container_id is not None:
            await _do_stop(container_id)
            stopped.append(victim)
    return stopped


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


@app.get("/gpu-budget")
async def gpu_budget(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_sidecar_token(authorization)
    participants = await _build_participants()
    return JSONResponse(budget_snapshot(participants, ceiling=_GPU_BUDGET_CEILING))


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
    profile_id = str(payload.get("profile", ""))
    target_profile = _catalog.profiles.get(profile_id)
    try:
        async with _budget_lock:
            # Refuse (with an eviction plan) if the target profile would oversubscribe
            # the GPU. Switching to a same-or-smaller profile fits in the main slot;
            # a larger one tells the operator what to stop first. (No auto-evict here:
            # a profile change is deliberate, so freeing room is an explicit step.)
            if target_profile is not None:
                await _admit_or_raise(_MAIN_SERVICE, target_profile.vram_fraction, force=False)
            operation_id = _main_model_manager.request_switch(
                profile_id,
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


@app.post("/main-model/stop")
async def main_model_stop(authorization: str | None = Header(default=None)) -> JSONResponse:
    """Drain and stop the main runtime to reclaim its VRAM (chat fails closed)."""
    await _require_sidecar_token(authorization)
    await _main_model_manager.stop_main()
    return JSONResponse({"action": "stop", "service": _MAIN_SERVICE, "runtime_state": "stopped"})


@app.post("/main-model/start")
async def main_model_start(
    force: bool = False,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Admit against the GPU budget, then start and validate the main runtime."""
    await _require_sidecar_token(authorization)
    snapshot = _main_model_manager.snapshot()
    active_profile = snapshot.get("active_profile") or {}
    target_fraction = float(
        active_profile.get("vram_fraction")
        or _catalog.profiles[_main_model_manager.boot_profile].vram_fraction
    )
    async with _budget_lock:
        evicted = await _admit_or_raise(_MAIN_SERVICE, target_fraction, force=force)
        await _main_model_manager.start_main()
    return JSONResponse(
        {"action": "start", "service": _MAIN_SERVICE, "runtime_state": "active", "evicted": evicted}
    )


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
    force: bool = False,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    await _require_sidecar_token(authorization)
    if service not in CONTROLLABLE:
        raise HTTPException(403, detail=f"not controllable: {service}")

    started: list[str] = []
    evicted: list[str] = []

    async with _budget_lock:
        # Admit the whole set being brought up (service + any not-yet-running
        # prerequisites) against the shared GPU budget before touching anything.
        to_start = [
            prereq
            for prereq in _START_PREREQUISITES.get(service, [])
            if await _container_status(prereq) != "running"
        ]
        if await _container_status(service) != "running":
            to_start.append(service)
        target_fraction = sum(_VRAM_FRACTION.get(svc, 0.0) for svc in to_start)
        if target_fraction > 0:
            evicted = await _admit_or_raise(service, target_fraction, force=force)

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

    return JSONResponse(
        {"action": "start", "service": service, "started": started, "evicted": evicted}
    )
