from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..main_model.docker_backend import DockerMainModelBackend
from ..configuration import load_yaml_mapping
from ..gpu_budget import Participant, budget_snapshot, plan_activation
from ..service_logging import service_logger
from ..main_model.control import (
    MainModelManager,
    MainModelStateError,
    MainModelStateStore,
    MainModelSwitchError,
    gpu_util_override_from_mapping,
    load_main_model_catalog,
)
from ..log_target_manifest import build_targets, write_manifest
from ..runtime_topology import load_runtime_topology

_logger = service_logger("admin_sidecar")

# --------------------------------------------------------------------- config

@dataclass(frozen=True)
class SidecarConfig:
    docker_socket: str
    compose_project: str
    config_root: Path
    state_path: Path
    boot_profile: str | None
    profile_locked: bool
    idempotency_ttl_seconds: str | None
    internal_service_token: str
    gateway_internal_url: str
    log_target_manifest_path: Path
    log_target_refresh_seconds: float


def _default_gateway_internal_url(config_root: Path) -> str:
    services = load_yaml_mapping(config_root / "configs/services.yaml")["services"]
    gateway = services["gateway"]
    return f"http://{gateway['compose_service']}:{gateway['container_port']}"


def load_sidecar_config(
    environment: Mapping[str, str], *, source_path: Path | None = None
) -> SidecarConfig:
    """Docker import나 백그라운드 작업 시작 없이 sidecar 설정을 해석한다."""
    configured_root = Path(environment.get("APP_CONFIG_ROOT", "/app"))
    using_local_config = not (configured_root / "configs/main_model_profiles.yaml").exists()
    config_root = (source_path or Path(__file__)).resolve().parents[3] if using_local_config else configured_root
    default_state_path = (
        config_root / ".runtime/main-model/main-model-state.json"
        if using_local_config
        else Path("/var/lib/ai-model-serving/main-model-state.json")
    )
    return SidecarConfig(
        docker_socket=environment.get("DOCKER_SOCKET", "/var/run/docker.sock"),
        compose_project=environment.get("COMPOSE_PROJECT", ""),
        config_root=config_root,
        state_path=Path(environment.get("MAIN_MODEL_STATE_PATH", str(default_state_path))),
        boot_profile=environment.get("MAIN_LLM_BOOT_PROFILE") or None,
        profile_locked=environment.get("MAIN_LLM_PROFILE_LOCKED", "false").lower() == "true",
        idempotency_ttl_seconds=environment.get("MAIN_LLM_SWITCH_IDEMPOTENCY_TTL_SECONDS") or None,
        internal_service_token=environment.get("INTERNAL_SERVICE_TOKEN", ""),
        gateway_internal_url=environment.get("GATEWAY_INTERNAL_URL") or _default_gateway_internal_url(config_root),
        log_target_manifest_path=Path(
            environment.get(
                "LOG_TARGET_MANIFEST_PATH",
                "/var/lib/ai-model-serving/log-targets/docker-containers.json",
            )
        ),
        log_target_refresh_seconds=float(environment.get("LOG_TARGET_REFRESH_SECONDS", "15")),
    )


_CONFIG = load_sidecar_config(os.environ)
DOCKER_SOCKET = _CONFIG.docker_socket
COMPOSE_PROJECT = _CONFIG.compose_project
APP_CONFIG_ROOT = _CONFIG.config_root
MAIN_MODEL_STATE_PATH = _CONFIG.state_path
MAIN_LLM_BOOT_PROFILE = _CONFIG.boot_profile
MAIN_LLM_PROFILE_LOCKED = _CONFIG.profile_locked
MAIN_LLM_SWITCH_IDEMPOTENCY_TTL_SECONDS = _CONFIG.idempotency_ttl_seconds
SIDECAR_TOKEN = _CONFIG.internal_service_token
LOG_TARGET_MANIFEST_PATH = _CONFIG.log_target_manifest_path
LOG_TARGET_REFRESH_SECONDS = _CONFIG.log_target_refresh_seconds

_TOPOLOGY = load_runtime_topology(
    APP_CONFIG_ROOT,
    compose_path=APP_CONFIG_ROOT / "ops/compose/full-stack.private-network.yaml",
)
CONTROLLABLE: frozenset[str] = _TOPOLOGY.controllable_services
_HEALTH_PORT: dict[str, int] = dict(_TOPOLOGY.health_port_by_service)
_START_PREREQUISITES: dict[str, list[str]] = dict(_TOPOLOGY.start_prerequisites_by_service)
_VRAM_FRACTION: dict[str, float] = dict(_TOPOLOGY.vram_fraction_by_service)
# 표준 GPU VRAM 예산은 configs/gpu_budgets.yaml에 있다(단일 소스이며
# modelctl/runtime_validation에서도 함께 사용된다). admission ceiling은
# 그 정책의 "avoid_above" 비율 값이다.
#
# 의도적으로 strict하게 읽는다(누락/오타 시 기본값으로 조용히 폴백하지 않고
# 바로 실패): 여기서 default(0.95)로 폴백하면 실제 정책값(0.93)보다 느슨한
# ceiling으로 admission이 동작하게 되어, GPU 예산 초과를 막아야 할 안전장치가
# 설정 오타 하나로 fail-open이 된다. runtime_validation/config_checks.py의
# 동일 키 검증(strict bracket access)과 동작을 맞춘다.
_gpu_budgets_path = APP_CONFIG_ROOT / "configs/gpu_budgets.yaml"
_gpu_budgets_cfg = load_yaml_mapping(_gpu_budgets_path)
_GPU_BUDGET_CEILING = float(_gpu_budgets_cfg["gpu"]["total_gpu_memory_utilization"]["avoid_above"])

_CRITICALITY: dict[str, str] = dict(_TOPOLOGY.criticality_by_service)
# criticality별 eviction 정책: priority가 높을수록 더 나중에 evict된다. 주 사용자
# 경로(primary user path, main)는 절대 자동 evict되지 않으며, risk/safety 경로는
# retrieval support보다 더 오래 유지된다. 알 수 없는 role은 기본 evictable tier에 속한다.
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


def _compose_project_filter() -> str:
    return json.dumps({"label": [f"com.docker.compose.project={COMPOSE_PROJECT}"]})


async def _refresh_log_targets() -> int:
    """현재 Compose 프로젝트의 실행 컨테이너를 Alloy target manifest로 투영한다.

    이 작업은 monitoring 보조 경로다. Docker API/파일 I/O 실패가 sidecar health나
    model control plane을 실패시키지 않도록 호출자가 예외를 기록하고 재시도한다.
    """
    if not COMPOSE_PROJECT:
        raise RuntimeError("COMPOSE_PROJECT is required for log target projection")
    async with _docker_client() as dc:
        listed = await dc.get(
            "/containers/json",
            params={"filters": _compose_project_filter()},
            timeout=5.0,
        )
        listed.raise_for_status()
        containers = listed.json()
        inspected: list[Mapping[str, Any]] = []
        for container in containers:
            container_id = str(container.get("Id") or "")
            if not container_id:
                continue
            response = await dc.get(f"/containers/{container_id}/json", timeout=5.0)
            response.raise_for_status()
            inspected.append(response.json())
    targets = build_targets(inspected)
    write_manifest(LOG_TARGET_MANIFEST_PATH, targets)
    return len(targets)


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
        return containers[0]["State"]  # "running", "exited", "paused" 등


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
    deadline = asyncio.get_running_loop().time() + timeout
    last_detail = "no response"
    async with httpx.AsyncClient(timeout=2.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return True
                last_detail = f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                last_detail = type(exc).__name__
            await asyncio.sleep(3.0)
    _logger.warning("runtime health check timed out: service=%s url=%s last_result=%s", service, url, last_detail)
    return False


_catalog = load_main_model_catalog(
    APP_CONFIG_ROOT / "configs/main_model_profiles.yaml",
    gpu_memory_utilization_override=gpu_util_override_from_mapping(os.environ),
    env=dict(os.environ),
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
        gateway_url=_CONFIG.gateway_internal_url,
        internal_token=SIDECAR_TOKEN,
    ),
    boot_profile=MAIN_LLM_BOOT_PROFILE,
    profile_locked=MAIN_LLM_PROFILE_LOCKED,
    idempotency_ttl_seconds=(
        float(MAIN_LLM_SWITCH_IDEMPOTENCY_TTL_SECONDS)
        if MAIN_LLM_SWITCH_IDEMPOTENCY_TTL_SECONDS
        else None
    ),
)
_MAIN_SERVICE = str(_catalog.runtime["compose_service"])
_initialization_error: str | None = None
_initialized = asyncio.Event()
# 백그라운드 task가 실행 도중 가비지 컬렉션되지 않도록 strong reference를 유지한다
# (asyncio는 스케줄된 task에 대해 weak reference만 유지한다).
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
# 두 activation이 동시에 통과할 수 없도록 GPU-budget admission 판단을 직렬화한다.
_budget_lock = asyncio.Lock()


async def _build_participants() -> list[Participant]:
    """현재 GPU 예산 참여자(보조 런타임과 main model)를 구성한다."""
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
    # 아직 active profile이 기록되지 않았을 때는(일반적인 0.9가 아니라) boot profile의
    # 예약값으로 폴백하여, ledger가 실제 main-model 비용을 반영하도록 한다.
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
    """공유 GPU 예산 안에서 런타임 활성화를 허용하거나 거부한다.

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
                "message": "GPU budget does not allow this activation.",
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
                "message": "GPU budget does not allow this activation.",
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
    except Exception as exc:  # noqa: BLE001 - /health를 통해 노출되며, 루프를 죽여서는 안 된다
        _initialization_error = str(exc)
    finally:
        _initialized.set()


# main-llm-vllm이 admin-sidecar 제어 API를 거치지 않고 재시작됐는지(예: 운영자의 수동
# `docker restart`) 주기적으로 감지하는 간격. 매 tick마다 lock 획득 + Docker inspect
# 호출 하나뿐이라 부담이 사실상 없어서, 놓치는 창을 줄이는 쪽으로 짧게 잡았다.
_RECONCILE_POLL_INTERVAL_SECONDS = 10


async def _run_reconciliation_loop() -> None:
    # reconcile_if_restarted() 자체가 예상 가능한 실패(inspect 실패 등)는 내부에서
    # 처리하고 다음 tick으로 넘어가므로, 여기서 잡히는 예외는 프로그래밍 오류에
    # 가깝다. 그래도 루프를 죽이지 않는다 — 죽으면 이후 재시작 감지가 프로세스
    # 수명 내내 영구히 멈춘다.
    while True:
        await asyncio.sleep(_RECONCILE_POLL_INTERVAL_SECONDS)
        try:
            await _main_model_manager.reconcile_if_restarted()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("main-model reconciliation tick failed: %s", exc)


async def _run_log_target_projection_loop() -> None:
    while True:
        try:
            target_count = await _refresh_log_targets()
            _logger.debug("refreshed Alloy log targets: count=%s", target_count)
        except Exception as exc:  # noqa: BLE001 - monitoring failure must not affect serving
            _logger.warning("log target projection tick failed: %s", exc)
        await asyncio.sleep(LOG_TARGET_REFRESH_SECONDS)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    del app
    # main-model reconciliation은 main-llm 런타임이 healthy 상태가 될 때까지
    # 최대 startup_timeout_seconds(600초)까지 블록될 수 있다. 여기서 이를 await하면
    # uvicorn의 시작이 지연되어, cold deploy 시 컨테이너의 약 40초 healthcheck
    # 예산을 넘겨 /health에 도달할 수 없게 되고 -> sidecar가 unhealthy로 표시되어
    # Gateway의 depends_on(service_healthy)이 전체 rollout을 실패시킨다.
    # sidecar는 control plane이다: 그 liveness는 main-llm 부팅과 무관하며,
    # main-llm 부팅 상태는 대신 gate(Gateway가 읽는다)로 추적된다.
    init_task = asyncio.create_task(_run_initialize())
    _BACKGROUND_TASKS.add(init_task)
    init_task.add_done_callback(_BACKGROUND_TASKS.discard)
    # 이 루프는 initialize()와 달리 main-llm이 healthy해지길 기다리지 않는다 —
    # 그냥 30초마다 자고 관측만 하므로 sidecar startup을 절대 지연시키지 않는다.
    reconcile_task = asyncio.create_task(_run_reconciliation_loop())
    _BACKGROUND_TASKS.add(reconcile_task)
    reconcile_task.add_done_callback(_BACKGROUND_TASKS.discard)
    log_target_task = asyncio.create_task(_run_log_target_projection_loop())
    _BACKGROUND_TASKS.add(log_target_task)
    log_target_task.add_done_callback(_BACKGROUND_TASKS.discard)
    try:
        yield
    finally:
        init_task.cancel()
        reconcile_task.cancel()
        log_target_task.cancel()


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
    # control-plane의 liveness는 main-llm 부팅과 의도적으로 분리되어 있다.
    # reconciliation이 진행 중인 동안에는 sidecar가 healthy 상태를 유지하고
    # gate는 닫힌 채로 있다(Gateway는 local-main에 대해 fail closed된다). 오직
    # *확정적인* reconciliation 실패만 unhealthy로 노출된다.
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
        # jsonable_encoder는 date/datetime(및 그 외 JSON 네이티브가 아닌 타입)을
        # 직렬화 가능한 형태로 변환한다. 이것이 없으면 profile catalog 안의 date
        # 값 하나(예: quote되지 않은 validated_at)만으로도 이 엔드포인트가 500을
        # 반환하게 되고, Gateway는 이를 SidecarUnavailable로 인식하여 모든
        # main-model 요청을 실패시킨다.
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
            # target profile이 GPU를 초과 사용하게 될 경우 (eviction plan과 함께) 거부한다.
            # 같거나 더 작은 profile로 전환하면 main slot에 그대로 들어맞지만, 더 큰 profile은
            # 운영자에게 먼저 무엇을 stop해야 하는지 알려준다. (여기서는 auto-evict를 하지
            # 않는다: profile 변경은 의도적인 행위이므로, 공간 확보는 명시적인 단계여야 한다.)
            if target_profile is not None:
                await _admit_or_raise(_MAIN_SERVICE, target_profile.vram_fraction, force=False)
            outcome = _main_model_manager.request_switch(
                profile_id,
                confirm_unverified=payload.get("confirm_unverified") is True,
                client_request_id=payload.get("request_id"),
            )
    except MainModelSwitchError as exc:
        raise HTTPException(
            exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    # 스스로 설명하는 응답: operation의 실제 상태를 보고한다(새 switch는 "pending"이고,
    # idempotent replay는 이전 operation의 상태를 그대로 갖는다) 그리고 새로운 switch가
    # 실제로 시작되었는지, 어디서 확인할 수 있는지를 명확히 알려준다.
    operation = _main_model_manager.operation(outcome.operation_id) or {}
    status = str(operation.get("status", "pending"))
    if outcome.reused:
        message = (
            f"request_id was already used; returning the existing operation "
            f"(status: {status}). No new switch was started — send a unique "
            f"request_id (or omit it) to start a new switch."
        )
    else:
        message = (
            "Switch accepted. Watch progress at "
            f"GET /admin/main-model/operations/{outcome.operation_id} or "
            "GET /admin/main-model (last_operation)."
        )
    return JSONResponse(
        {
            "operation_id": outcome.operation_id,
            "status": status,
            "reused": outcome.reused,
            "message": message,
        },
        status_code=202,
    )


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
    """VRAM 회수를 위해 main runtime을 drain 후 중지하고 chat 요청은 fail-closed 처리한다."""
    await _require_sidecar_token(authorization)
    await _main_model_manager.stop_main()
    return JSONResponse({"action": "stop", "service": _MAIN_SERVICE, "runtime_state": "stopped"})


@app.post("/main-model/start")
async def main_model_start(
    force: bool = False,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """GPU 예산을 확인한 뒤 main runtime을 시작하고 검증한다."""
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
        # 아무것도 건드리기 전에, 함께 기동되는 전체 집합(service + 아직 실행 중이 아닌
        # prerequisite들)을 공유 GPU budget에 대해 admit한다. 실제 start/health
        # 시퀀스 동안에도 lock을 유지하여, 다른 동시 activation이 동일한 pre-start
        # snapshot을 기준으로 admission을 통과해 VRAM을 overcommit하는 일이 없도록 한다.
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

        # prerequisite들을 순서대로 시작한다(GPU 메모리 프로파일링을 순차적으로 진행).
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
        if await _container_status(service) != "running":
            await _do_start(container_id)
            started.append(service)
        target_port = _HEALTH_PORT[service]
        if not await _wait_healthy(service, target_port):
            raise HTTPException(
                503, detail=f"container {service} did not become healthy within timeout"
            )

    return JSONResponse(
        {"action": "start", "service": service, "started": started, "evicted": evicted}
    )
