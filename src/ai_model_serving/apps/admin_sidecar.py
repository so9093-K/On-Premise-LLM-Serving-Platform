from __future__ import annotations

import asyncio
import json
import os

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

# --------------------------------------------------------------------- config

DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT", "")

# Only these compose service names can be started/stopped.
CONTROLLABLE: frozenset[str] = frozenset({
    "embedding-vllm",
    "embedding-ko-vllm",
    "risk-prompt-vllm",
})

# Serial GPU startup: before starting B, ensure A is healthy first.
# Keys are in the correct start order; values are (service_name, health_port).
_START_PREREQUISITES: dict[str, list[tuple[str, int]]] = {
    "embedding-ko-vllm": [("embedding-vllm", 9402)],
    "risk-prompt-vllm": [("embedding-vllm", 9402), ("embedding-ko-vllm", 9406)],
}

_HEALTH_PORT: dict[str, int] = {
    "embedding-vllm": 9402,
    "embedding-ko-vllm": 9406,
    "risk-prompt-vllm": 9403,
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


# --------------------------------------------------------------------- app

app = FastAPI(
    title="Admin Sidecar",
    description="Internal container lifecycle control. Not exposed publicly.",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/containers/status")
async def containers_status() -> JSONResponse:
    statuses: dict[str, str] = {}
    for service in CONTROLLABLE:
        try:
            statuses[service] = await _container_status(service)
        except Exception as exc:
            statuses[service] = f"error: {exc}"
    return JSONResponse({"containers": statuses})


@app.post("/containers/{service}/stop")
async def stop_container(service: str) -> JSONResponse:
    if service not in CONTROLLABLE:
        raise HTTPException(403, detail=f"not controllable: {service}")
    container_id = await _find_container_id(service)
    if container_id is None:
        raise HTTPException(404, detail=f"container not found: {service}")
    await _do_stop(container_id)
    return JSONResponse({"action": "stop", "service": service, "stopped": [service]})


@app.post("/containers/{service}/start")
async def start_container(service: str) -> JSONResponse:
    if service not in CONTROLLABLE:
        raise HTTPException(403, detail=f"not controllable: {service}")

    started: list[str] = []

    # Start prerequisites in order (serial GPU memory profiling)
    for prereq, prereq_port in _START_PREREQUISITES.get(service, []):
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
