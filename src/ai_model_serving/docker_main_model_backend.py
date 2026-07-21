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

# --enable-auto-tool-choice --tool-call-parser gemma4도 xgrammar 기반 제약 디코딩을
# 거치므로, 위 json_schema 웜업과 같은 Triton JIT 이슈(apply_token_bitmask_inplace_kernel)를
# 별도로 겪을 수 있다 — 다만 두 경로가 같은 커널을 공유한다면(Triton JIT은 보통 커널
# 단위로 캐싱되지 스키마 내용 단위가 아니다) 이미 위 웜업으로 예열됐을 수도 있다.
# 실제로 별개인지는 미확인이라, 이 호출 자체가 가장 싼 검증 수단이기도 하다: 배포
# 로그에 새 JIT 이벤트가 뜨면 별개였다는 뜻이고, 안 뜨면 이미 커버되고 있었다는 뜻이다.
_TOOL_CALL_WARMUP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "warmup",
            "description": "Warmup no-op tool.",
            "parameters": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
        },
    }
]


_AUDIO_CANARY_M4A_B64 = TINY_M4A_AAC_B64
_VIDEO_CANARY_MP4_B64 = TINY_MP4_VIDEO_B64

# Docker Engine API HTTP 호출 타임아웃(초 단위). 이 값들은 로컬 docker 소켓에 대한
# 요청만을 제한하며, 모델 lifecycle(drain/stop/startup)은 catalog.runtime에 있는
# 프로필 자체의 타임아웃으로 관리된다.
_DOCKER_INSPECT_TIMEOUT = 5          # 가벼운 list/inspect GET
_DOCKER_API_TIMEOUT = 30             # 컨테이너 create / start
_DOCKER_DELETE_TIMEOUT = 10          # 컨테이너 delete
_DOCKER_STOP_TIMEOUT_BUFFER = 10     # stop HTTP 타임아웃 = docker stop grace + 이 값
_DRAIN_POLL_CLIENT_TIMEOUT = 3       # drain-status poll 요청 1회당
_DRAIN_POLL_INTERVAL_SECONDS = 0.25  # drain-status poll 사이 간격
_HEALTH_POLL_INTERVAL_SECONDS = 3    # 컨테이너 health poll 사이 간격
_RUNTIME_HTTP_TIMEOUT = 30           # vLLM 런타임으로의 HTTP 요청(/v1/models, canary)


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
        # Hugging Face는 저장소를 *hub* 캐시(HF_HOME/hub) 아래에 저장하며, vLLM도
        # 이 위치에서 조회한다. snapshot_download(cache_dir=X)는 X/models--...에
        # 기록하므로, cache_dir은 HF_HOME이 아니라 반드시 hub 디렉터리여야 한다.
        # 그렇지 않으면 준비된 snapshot이 런타임이 resolve하는 위치보다 한 단계 위에
        # 놓이게 된다.
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

    async def observed_started_at(self, catalog: MainModelCatalog) -> str | None:
        """Return the running container's Docker State.StartedAt, or None if absent.

        Used by MainModelManager.reconcile_if_restarted() to detect a container
        restart that bypassed this controller entirely (e.g. `docker restart`
        run directly instead of through the admin API) — such a restart resets
        the vLLM process's Triton JIT cache but this controller would otherwise
        never notice and never re-run the warmup in validate().
        """
        service = str(catalog.runtime["compose_service"])
        container_id = await self._container_id(service)
        if container_id is None:
            return None
        inspected = await self._inspect(container_id)
        started_at = inspected.get("State", {}).get("StartedAt")
        return str(started_at) if started_at else None

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
        # 공유 기본 이미지 대신 active profile의 resolve된 이미지를 사용한다(프로필이
        # 자체 런타임을 고정할 수 있다, 예: audio 지원 빌드). loader는 profile.image가
        # digest로 고정되어 있고 비어있지 않음을 보장한다.
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
            # Best-effort structured-output warmup: vLLM는 xgrammar 제약 디코딩
            # Triton 커널(apply_token_bitmask_inplace_kernel)을 최초 호출 시 JIT
            # 컴파일한다. 위의 text canary는 response_format을 설정하지 않으므로,
            # 이 호출이 없으면 그 컴파일 비용은 response_format=json_schema를 처음
            # 사용하는 실제 요청이 떠안게 되어, structured-output 클라이언트 입장에서
            # 원인 불명의 느리거나 잘린 "첫 시도"로 나타난다. 이 호출은 그 비용을
            # 미리 지불할 뿐이므로 switch를 실패시켜서는 안 되며(warmup이 느리거나
            # 실패한다고 해서 정상 동작하는 profile을 rollback할 이유는 없다),
            # 예외는 raise하지 않고 로그만 남긴다.
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
            # Best-effort tool-calling warmup: 위 json_schema 웜업과 같은 이유(별개의
            # JIT 이벤트일 수도, 이미 커버됐을 수도 있음 — _TOOL_CALL_WARMUP_TOOLS 주석
            # 참고). tool_choice를 특정 함수로 강제해 실제로 제약 디코딩 경로를 태운다
            # (그냥 tools만 실어 보내면 모델이 tool을 안 쓰고 일반 텍스트로 답할 수 있어
            # 이 경로를 타지 않을 수 있다).
            try:
                await client.post(
                    base + str(catalog.runtime["chat_path"]),
                    json={
                        "model": catalog.public_model,
                        "messages": [{"role": "user", "content": "Call the warmup tool."}],
                        "max_tokens": 16,
                        "temperature": 0,
                        "tools": _TOOL_CALL_WARMUP_TOOLS,
                        "tool_choice": {"type": "function", "function": {"name": "warmup"}},
                    },
                )
            except httpx.HTTPError as exc:
                _logger.warning("tool-calling warmup canary failed (non-fatal): %s", exc)
            # Media boot canary: 해당 modality를 실제로 배포하는 profile에 대해서만
            # 수행한다. gate가 열리기 전에 런타임이 컨테이너 media를 디코드할 수
            # 있음을 증명하여, 지원한다고 표시되었지만 실제로는 깨진 modality가
            # 실제 요청에서 500을 내는 대신 rollback되도록 한다.
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
