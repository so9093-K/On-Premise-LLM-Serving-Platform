from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from typing import Any

import httpx

from .logging_policy import service_logger
from .main_model_control import MainModelCatalog, MainModelProfile
from .media_samples import TINY_M4A_AAC_B64, TINY_MP4_VIDEO_B64
from .model_cache import prepare_model_snapshot

_logger = service_logger("main_model_control")

_STRUCTURED_OUTPUT_WARMUP_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "warmup",
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        "strict": True,
    },
}


_AUDIO_CANARY_M4A_B64 = TINY_M4A_AAC_B64
_VIDEO_CANARY_MP4_B64 = TINY_MP4_VIDEO_B64

# Docker Engine API HTTP-call timeouts (seconds). These bound the request to the
# local docker socket only; the model lifecycle (drain/stop/startup) is governed
# by the profile's own timeouts in catalog.runtime.
_DOCKER_INSPECT_TIMEOUT = 5          # cheap list/inspect GET
_DOCKER_API_TIMEOUT = 30             # create / start container
_DOCKER_DELETE_TIMEOUT = 10          # delete container
_DOCKER_STOP_TIMEOUT_BUFFER = 10     # stop HTTP timeout = docker stop grace + this
_DRAIN_POLL_CLIENT_TIMEOUT = 3       # each drain-status poll request
_DRAIN_POLL_INTERVAL_SECONDS = 0.25  # between drain-status polls
_HEALTH_POLL_INTERVAL_SECONDS = 3    # between container health polls
_RUNTIME_HTTP_TIMEOUT = 30           # HTTP to the vLLM runtime (/v1/models, canary)


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
        cache_dir: str | None = None,
    ) -> None:
        self.docker_socket = docker_socket
        self.compose_project = compose_project
        self.gateway_url = gateway_url.rstrip("/")
        self.internal_headers = (
            {"Authorization": f"Bearer {internal_token}"} if internal_token else {}
        )
        # Hugging Face stores repos under the *hub* cache (HF_HOME/hub), which is
        # where vLLM looks them up. snapshot_download(cache_dir=X) writes to
        # X/models--..., so cache_dir must be the hub dir, not HF_HOME, or the
        # prepared snapshot lands one level above where the runtime resolves it.
        _hf_home = os.environ.get("HF_HOME", "/root/.cache/huggingface")
        self.cache_dir = Path(
            cache_dir
            or os.environ.get("HF_HUB_CACHE")
            or os.path.join(_hf_home, "hub")
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
                timeout=_DOCKER_INSPECT_TIMEOUT,
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
            response = await client.get(f"/containers/{container_id}/json", timeout=_DOCKER_INSPECT_TIMEOUT)
            response.raise_for_status()
            return response.json()

    async def is_running(self, catalog: MainModelCatalog) -> bool:
        service = str(catalog.runtime["compose_service"])
        container_id = await self._container_id(service)
        if container_id is None:
            return False
        inspected = await self._inspect(container_id)
        return inspected.get("State", {}).get("Status") == "running"

    async def stop(self, catalog: MainModelCatalog) -> None:
        """Stop (but keep) the main runtime container to reclaim its VRAM."""
        service = str(catalog.runtime["compose_service"])
        container_id = await self._container_id(service)
        if container_id is None:
            return
        grace = int(catalog.runtime.get("stop_timeout_seconds", 30))
        async with self._client() as client:
            stopped = await client.post(
                f"/containers/{container_id}/stop",
                params={"t": grace},
                timeout=grace + _DOCKER_STOP_TIMEOUT_BUFFER,
            )
            if stopped.status_code not in (204, 304):
                stopped.raise_for_status()

    async def start(self, catalog: MainModelCatalog) -> None:
        """Start the existing (stopped) main runtime container with its profile."""
        service = str(catalog.runtime["compose_service"])
        container_id = await self._container_id(service)
        if container_id is None:
            raise RuntimeError("main runtime container not found to start")
        async with self._client() as client:
            started = await client.post(f"/containers/{container_id}/start", timeout=_DOCKER_API_TIMEOUT)
            if started.status_code not in (204, 304):
                started.raise_for_status()

    async def observed_profile(self, catalog: MainModelCatalog) -> str | None:
        service = str(catalog.runtime["compose_service"])
        container_id = await self._container_id(service)
        if container_id is None:
            return None
        inspected = await self._inspect(container_id)
        config = inspected.get("Config", {})
        command = list(config.get("Cmd") or [])
        image = config.get("Image")
        model = command[command.index("--model") + 1] if "--model" in command else None
        revision = command[command.index("--revision") + 1] if "--revision" in command else None
        for profile in catalog.profiles.values():
            if (
                profile.model_id == model
                and (revision is None or revision == profile.revision)
                and image == profile.image
            ):
                return profile.profile_id
        return None

    async def prepare(
        self, catalog: MainModelCatalog, profile: MainModelProfile
    ) -> None:
        del catalog
        await asyncio.to_thread(
            prepare_model_snapshot,
            model_id=profile.model_id,
            revision=profile.revision,
            cache_dir=self.cache_dir,
        )

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
        async with httpx.AsyncClient(timeout=_DRAIN_POLL_CLIENT_TIMEOUT) as client:
            while asyncio.get_running_loop().time() < deadline:
                response = await client.get(
                    f"{self.gateway_url}/internal/main-model/drain-status",
                    headers=self.internal_headers,
                )
                response.raise_for_status()
                last_in_flight = int(response.json().get("in_flight", -1))
                if last_in_flight == 0:
                    return
                await asyncio.sleep(_DRAIN_POLL_INTERVAL_SECONDS)
        raise RuntimeError(
            f"main-model request drain timed out with in_flight={last_in_flight}"
        )

    async def replace(self, catalog: MainModelCatalog, profile: MainModelProfile) -> None:
        service = str(catalog.runtime["compose_service"])
        container_id = await self._container_id(service)
        if container_id is not None:
            inspected = await self._inspect(container_id)
            self._template = self._creation_template(inspected)
            grace = int(catalog.runtime.get("stop_timeout_seconds", 30))
            async with self._client() as client:
                stopped = await client.post(
                    f"/containers/{container_id}/stop",
                    params={"t": grace},
                    timeout=grace + _DOCKER_STOP_TIMEOUT_BUFFER,
                )
                if stopped.status_code not in (204, 304):
                    stopped.raise_for_status()
                removed = await client.delete(f"/containers/{container_id}", timeout=_DOCKER_DELETE_TIMEOUT)
                if removed.status_code != 204:
                    removed.raise_for_status()
        if self._template is None:
            raise RuntimeError("main runtime container template is unavailable")

        payload = copy.deepcopy(self._template["config"])
        # Use the active profile's resolved image (a profile may pin its own
        # runtime, e.g. an audio-capable build) rather than the shared default.
        # The loader guarantees profile.image is digest-pinned and non-empty.
        payload["Image"] = str(profile.image)
        payload["Cmd"] = list(profile.command)
        payload["HostConfig"] = copy.deepcopy(self._template["host_config"])
        payload["NetworkingConfig"] = copy.deepcopy(self._template["networking_config"])
        async with self._client() as client:
            created = await client.post(
                "/containers/create",
                params={"name": self._template["name"]},
                json=payload,
                timeout=_DOCKER_API_TIMEOUT,
            )
            created.raise_for_status()
            new_id = created.json()["Id"]
            started = await client.post(f"/containers/{new_id}/start", timeout=_DOCKER_API_TIMEOUT)
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
            await asyncio.sleep(_HEALTH_POLL_INTERVAL_SECONDS)
        else:
            raise RuntimeError(f"main runtime did not become healthy: {last_health}")

        port = int(catalog.runtime["container_port"])
        base = f"http://{service}:{port}"
        async with httpx.AsyncClient(timeout=_RUNTIME_HTTP_TIMEOUT) as client:
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
            # Best-effort structured-output warmup: vLLM JIT-compiles the xgrammar
            # constrained-decoding Triton kernel (apply_token_bitmask_inplace_kernel)
            # on its first invocation. The text canary above never sets
            # response_format, so without this call that compile lands on whichever
            # real request happens to be the first to use response_format=json_schema,
            # showing up as an unexplained slow/truncated "first attempt" for
            # structured-output clients. This call only pre-pays that cost; it must
            # not fail the switch (a slow/failed warmup is not a reason to roll back
            # an otherwise-working profile), so exceptions are logged, not raised.
            try:
                await client.post(
                    base + str(catalog.runtime["chat_path"]),
                    json={
                        "model": catalog.public_model,
                        "messages": [{"role": "user", "content": "Reply with {\"ok\": true}."}],
                        "max_tokens": 16,
                        "temperature": 0,
                        "response_format": _STRUCTURED_OUTPUT_WARMUP_SCHEMA,
                    },
                )
            except httpx.HTTPError as exc:
                _logger.warning("structured-output warmup canary failed (non-fatal): %s", exc)
            # Media boot canaries: only for profiles that actually deploy the
            # modality. These prove the runtime can decode container media before
            # the gate opens, so an advertised-but-broken modality rolls back
            # instead of 500-ing live requests.
            if profile.capabilities.get("audio_enabled"):
                audio_canary = await client.post(
                    base + str(catalog.runtime["chat_path"]),
                    json={
                        "model": catalog.public_model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Reply with OK only."},
                                    {
                                        "type": "input_audio",
                                        "input_audio": {"data": _AUDIO_CANARY_M4A_B64, "format": "m4a"},
                                    },
                                ],
                            }
                        ],
                        "max_tokens": 8,
                        "temperature": 0,
                    },
                )
                audio_canary.raise_for_status()
                audio_body = audio_canary.json()
                audio_choices = audio_body.get("choices", [])
                if not audio_choices or not audio_choices[0].get("message", {}).get("content"):
                    raise RuntimeError("main runtime audio canary returned no content")
            if profile.capabilities.get("video_enabled"):
                video_canary = await client.post(
                    base + str(catalog.runtime["chat_path"]),
                    json={
                        "model": catalog.public_model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Reply with OK only."},
                                    {
                                        "type": "video_url",
                                        "video_url": {"url": f"data:video/mp4;base64,{_VIDEO_CANARY_MP4_B64}"},
                                    },
                                ],
                            }
                        ],
                        "max_tokens": 8,
                        "temperature": 0,
                    },
                )
                video_canary.raise_for_status()
                video_body = video_canary.json()
                video_choices = video_body.get("choices", [])
                if not video_choices or not video_choices[0].get("message", {}).get("content"):
                    raise RuntimeError("main runtime video canary returned no content")
