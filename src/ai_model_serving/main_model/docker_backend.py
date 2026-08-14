from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from typing import Any

import httpx

from ..contracts.chat_response import validate_chat_response
from ..errors import ServiceError
from .control import MainModelCatalog, MainModelProfile
from ..media_samples import TINY_JPEG_1X1_B64, TINY_M4A_AAC_B64, TINY_MP4_VIDEO_B64
from .cache import prepare_model_snapshot

_IMAGE_CANARY_JPEG_B64 = TINY_JPEG_1X1_B64
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


def _validate_inference_canary(payload: object, *, expected_model: str, kind: str) -> None:
    """전환 canary가 실제 Gateway 응답 계약의 최소 형태를 충족하는지 확인한다."""
    try:
        body = validate_chat_response(payload, expected_model=expected_model)
    except ServiceError as exc:
        raise RuntimeError(f"main runtime {kind} canary returned an invalid completion: {exc.message}") from exc
    choices = body["choices"]
    content = choices[0]["message"].get("content")
    if not isinstance(content, str) or not content:
        raise RuntimeError(f"main runtime {kind} canary returned no content")

class DockerMainModelBackend:
    """허용 목록에 있는 Compose main-model 컨테이너만 재생성한다.

    호출자가 image, command, 컨테이너명, project명, mount, network를 지정할 수 없게
    하며, 이 값들은 profile catalog 또는 검사한 허용 Compose 컨테이너에서만 가져온다.
    """

    def __init__(
        self,
        docker_socket: str,
        compose_project: str = "",
        *,
        gateway_url: str,
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
        """실행 중인 컨테이너의 Docker ``State.StartedAt``을 반환하고 없으면 ``None``을 반환한다.

        관리자 API를 거치지 않은 컨테이너 재시작을 감지하고, 활성 Profile의 실제
        health·모델 식별·입력 capability를 다시 검증하는 데 사용한다.
        """
        service = str(catalog.runtime["compose_service"])
        container_id = await self._container_id(service)
        if container_id is None:
            return None
        inspected = await self._inspect(container_id)
        started_at = inspected.get("State", {}).get("StartedAt")
        return str(started_at) if started_at else None

    async def stop(self, catalog: MainModelCatalog) -> None:
        """컨테이너는 유지한 채 main runtime을 중지해 VRAM을 회수한다."""
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
        """기존에 중지된 main runtime 컨테이너를 저장된 프로필로 시작한다."""
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
            _validate_inference_canary(canary.json(), expected_model=catalog.public_model, kind="text")
            # Media boot canary: 해당 modality를 실제로 배포하는 profile에 대해서만
            # 수행한다. gate가 열리기 전에 런타임이 컨테이너 media를 디코드할 수
            # 있음을 증명하여, 지원한다고 표시되었지만 실제로는 깨진 modality가
            # 실제 요청에서 500을 내는 대신 rollback되도록 한다. 입력 modality의
            # 단일 source of truth는 capabilities.deployed_input이다 -- 별도
            # audio_enabled/video_enabled 플래그는 이 값과 항상 일치해야 하는
            # 중복 정보라 두지 않는다.
            deployed_modalities = set(profile.capabilities.get("deployed_input", []))
            if "image" in deployed_modalities:
                image_canary = await client.post(
                    base + str(catalog.runtime["chat_path"]),
                    json={
                        "model": catalog.public_model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Reply with OK only."},
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:image/jpeg;base64,{_IMAGE_CANARY_JPEG_B64}"},
                                    },
                                ],
                            }
                        ],
                        "max_tokens": 8,
                        "temperature": 0,
                    },
                )
                image_canary.raise_for_status()
                _validate_inference_canary(image_canary.json(), expected_model=catalog.public_model, kind="image")
            if "audio" in deployed_modalities:
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
                _validate_inference_canary(audio_canary.json(), expected_model=catalog.public_model, kind="audio")
            if "video" in deployed_modalities:
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
                _validate_inference_canary(video_canary.json(), expected_model=catalog.public_model, kind="video")
