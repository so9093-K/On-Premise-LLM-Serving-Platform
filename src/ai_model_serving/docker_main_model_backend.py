from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import httpx

from .main_model_control import MainModelCatalog, MainModelProfile


class DockerMainModelBackend:
    """Recreate only the allowlisted Compose main-model container.

    The backend never accepts a caller-supplied image, command, container name,
    project name, mount, or network. Those values come from the profile catalog
    or the inspected allowlisted Compose container.
    """

    def __init__(
        self,
        docker_socket: str,
        compose_project: str = "",
        *,
        gateway_url: str = "http://gateway:9400",
        internal_token: str = "",
    ) -> None:
        self.docker_socket = docker_socket
        self.compose_project = compose_project
        self.gateway_url = gateway_url.rstrip("/")
        self.internal_headers = (
            {"Authorization": f"Bearer {internal_token}"} if internal_token else {}
        )
        self._template: dict[str, Any] | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=self.docker_socket),
            base_url="http://docker",
        )

    def _filters(self, service: str) -> str:
        labels = [f"com.docker.compose.service={service}"]
        if self.compose_project:
            labels.append(f"com.docker.compose.project={self.compose_project}")
        return json.dumps({"label": labels})

    async def _container_ids(self, service: str) -> list[str]:
        async with self._client() as client:
            response = await client.get(
                "/containers/json",
                params={"all": "true", "filters": self._filters(service)},
                timeout=5,
            )
            response.raise_for_status()
            rows = response.json()
            return [str(row["Id"]) for row in rows]

    async def _container_id(self, service: str) -> str | None:
        ids = await self._container_ids(service)
        if len(ids) > 1:
            raise RuntimeError(
                f"multiple containers found for allowlisted service {service}: {len(ids)}"
            )
        return ids[0] if ids else None

    async def _inspect(self, container_id: str) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.get(f"/containers/{container_id}/json", timeout=5)
            response.raise_for_status()
            return response.json()

    async def observed_profile(self, catalog: MainModelCatalog) -> str | None:
        service = str(catalog.runtime["compose_service"])
        container_id = await self._container_id(service)
        if container_id is None:
            return None
        inspected = await self._inspect(container_id)
        command = list(inspected.get("Config", {}).get("Cmd") or [])
        model = command[command.index("--model") + 1] if "--model" in command else None
        revision = command[command.index("--revision") + 1] if "--revision" in command else None
        for profile in catalog.profiles.values():
            if profile.model_id == model and (revision is None or revision == profile.revision):
                return profile.profile_id
        return None

    @staticmethod
    def _creation_template(inspected: dict[str, Any]) -> dict[str, Any]:
        config = inspected["Config"]
        host = inspected["HostConfig"]
        networks = inspected.get("NetworkSettings", {}).get("Networks", {})
        endpoints: dict[str, Any] = {}
        for name, value in networks.items():
            endpoints[name] = {
                key: copy.deepcopy(value[key])
                for key in ("Aliases", "Links", "IPAMConfig", "DriverOpts", "GwPriority")
                if value.get(key) is not None
            }
        host_keys = (
            "Binds",
            "NetworkMode",
            "RestartPolicy",
            "AutoRemove",
            "VolumeDriver",
            "VolumesFrom",
            "CapAdd",
            "CapDrop",
            "CgroupnsMode",
            "Dns",
            "DnsOptions",
            "DnsSearch",
            "ExtraHosts",
            "GroupAdd",
            "IpcMode",
            "PidMode",
            "Privileged",
            "ReadonlyRootfs",
            "SecurityOpt",
            "ShmSize",
            "Sysctls",
            "Ulimits",
            "DeviceRequests",
            "PortBindings",
        )
        host_config = {key: copy.deepcopy(host[key]) for key in host_keys if host.get(key) is not None}
        return {
            "name": inspected["Name"].lstrip("/"),
            "config": {
                key: copy.deepcopy(config[key])
                for key in (
                    "Entrypoint",
                    "Env",
                    "WorkingDir",
                    "User",
                    "Labels",
                    "Healthcheck",
                    "ExposedPorts",
                    "StopSignal",
                    "StopTimeout",
                    "Tty",
                    "OpenStdin",
                )
                if config.get(key) is not None
            },
            "host_config": host_config,
            "networking_config": {"EndpointsConfig": endpoints},
        }

    async def wait_for_drain(self, timeout_seconds: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_in_flight: int | None = None
        async with httpx.AsyncClient(timeout=3) as client:
            while asyncio.get_running_loop().time() < deadline:
                response = await client.get(
                    f"{self.gateway_url}/internal/main-model/drain-status",
                    headers=self.internal_headers,
                )
                response.raise_for_status()
                last_in_flight = int(response.json().get("in_flight", -1))
                if last_in_flight == 0:
                    return
                await asyncio.sleep(0.25)
        raise RuntimeError(
            f"main-model request drain timed out with in_flight={last_in_flight}"
        )

    async def replace(self, catalog: MainModelCatalog, profile: MainModelProfile) -> None:
        service = str(catalog.runtime["compose_service"])
        container_id = await self._container_id(service)
        if container_id is not None:
            inspected = await self._inspect(container_id)
            self._template = self._creation_template(inspected)
            async with self._client() as client:
                stopped = await client.post(
                    f"/containers/{container_id}/stop",
                    params={"t": int(catalog.runtime.get("stop_timeout_seconds", 30))},
                    timeout=40,
                )
                if stopped.status_code not in (204, 304):
                    stopped.raise_for_status()
                removed = await client.delete(f"/containers/{container_id}", timeout=10)
                if removed.status_code != 204:
                    removed.raise_for_status()
        if self._template is None:
            raise RuntimeError("main runtime container template is unavailable")

        payload = copy.deepcopy(self._template["config"])
        payload["Image"] = str(catalog.runtime["image"])
        payload["Cmd"] = list(profile.command)
        payload["HostConfig"] = copy.deepcopy(self._template["host_config"])
        payload["NetworkingConfig"] = copy.deepcopy(self._template["networking_config"])
        async with self._client() as client:
            created = await client.post(
                "/containers/create",
                params={"name": self._template["name"]},
                json=payload,
                timeout=30,
            )
            created.raise_for_status()
            new_id = created.json()["Id"]
            started = await client.post(f"/containers/{new_id}/start", timeout=30)
            if started.status_code != 204:
                started.raise_for_status()

    async def validate(self, catalog: MainModelCatalog, profile: MainModelProfile) -> None:
        service = str(catalog.runtime["compose_service"])
        timeout = float(catalog.runtime.get("startup_timeout_seconds", 600))
        deadline = asyncio.get_running_loop().time() + timeout
        last_health = "unknown"
        while asyncio.get_running_loop().time() < deadline:
            container_id = await self._container_id(service)
            if container_id:
                inspected = await self._inspect(container_id)
                state = inspected.get("State", {})
                last_health = state.get("Health", {}).get("Status", state.get("Status", "unknown"))
                if last_health == "healthy":
                    break
                if state.get("Status") == "exited":
                    raise RuntimeError(
                        f"main runtime exited during startup: {state.get('ExitCode')}"
                    )
            await asyncio.sleep(3)
        else:
            raise RuntimeError(f"main runtime did not become healthy: {last_health}")

        port = int(catalog.runtime["container_port"])
        base = f"http://{service}:{port}"
        async with httpx.AsyncClient(timeout=30) as client:
            models = await client.get(base + str(catalog.runtime["models_path"]))
            models.raise_for_status()
            ids = {row.get("id") for row in models.json().get("data", [])}
            if catalog.public_model not in ids:
                raise RuntimeError(f"{catalog.public_model} missing from /v1/models")
            canary = await client.post(
                base + str(catalog.runtime["chat_path"]),
                json={
                    "model": catalog.public_model,
                    "messages": [{"role": "user", "content": "Reply with OK only."}],
                    "max_tokens": 8,
                    "temperature": 0,
                },
            )
            canary.raise_for_status()
            body = canary.json()
            choices = body.get("choices", [])
            if not choices or not choices[0].get("message", {}).get("content"):
                raise RuntimeError("main runtime inference canary returned no content")
