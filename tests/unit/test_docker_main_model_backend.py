"""DockerMainModelBackend의 결정론적 단위 테스트.

Docker daemon을 띄우지 않고 inspect·HTTP 요청을 fake로 대체한다. 컨테이너 재생성
정책과 Profile 기반 health·media canary 계약을 보호하므로 기본 `make test`에 포함한다.
"""

from __future__ import annotations

import asyncio

import httpx

import ai_model_serving.main_model.docker_backend as backend_module
from ai_model_serving.main_model.docker_backend import DockerMainModelBackend
from ai_model_serving.main_model.control import load_main_model_catalog

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_creation_template_copies_only_allowlisted_container_fields():
    inspected = {
        "Name": "/compose-main-llm-vllm-1",
        "Config": {
            "Entrypoint": ["vllm", "serve"],
            "Env": ["HF_HOME=/cache"],
            "WorkingDir": "",
            "User": "",
            "Labels": {"com.docker.compose.service": "main-llm-vllm"},
            "Healthcheck": {"Test": ["CMD", "true"]},
            "ExposedPorts": {"9401/tcp": {}},
            "Cmd": ["--model", "untrusted/current"],
            "Image": "untrusted:tag",
            "Hostname": "must-not-copy",
        },
        "HostConfig": {
            "Binds": ["/cache:/cache"],
            "NetworkMode": "compose_default",
            "RestartPolicy": {"Name": "no"},
            "DeviceRequests": [{"Count": -1, "Capabilities": [["gpu"]]}],
            "Privileged": False,
            "PortBindings": {"9401/tcp": [{"HostPort": "9999"}]},
        },
        "NetworkSettings": {
            "Networks": {
                "compose_default": {
                    "Aliases": ["main-llm-vllm"],
                    "IPAddress": "172.18.0.3",
                }
            }
        },
    }
    template = DockerMainModelBackend._creation_template(inspected)
    assert template["name"] == "compose-main-llm-vllm-1"
    assert "Cmd" not in template["config"]
    assert "Image" not in template["config"]
    assert "Hostname" not in template["config"]
    assert template["host_config"]["PortBindings"] == {
        "9401/tcp": [{"HostPort": "9999"}]
    }
    assert template["host_config"]["DeviceRequests"][0]["Capabilities"] == [["gpu"]]
    assert template["networking_config"]["EndpointsConfig"]["compose_default"] == {
        "Aliases": ["main-llm-vllm"]
    }


def test_creation_template_preserves_private_network_without_host_binding():
    inspected = {
        "Name": "/compose-main-llm-vllm-1",
        "Config": {
            "Entrypoint": ["vllm", "serve"],
            "Env": [],
            "Labels": {"com.docker.compose.service": "main-llm-vllm"},
            "ExposedPorts": {"9401/tcp": {}},
        },
        "HostConfig": {
            "NetworkMode": "compose_default",
            "RestartPolicy": {"Name": "no"},
            "DeviceRequests": [{"Count": -1, "Capabilities": [["gpu"]]}],
            "PortBindings": {},
        },
        "NetworkSettings": {
            "Networks": {"compose_default": {"Aliases": ["main-llm-vllm"]}}
        },
    }
    template = DockerMainModelBackend._creation_template(inspected)
    assert template["host_config"]["PortBindings"] == {}


def test_prepare_uses_profile_identity_and_shared_cache_path(tmp_path, monkeypatch):
    calls = []

    def fake_prepare(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(backend_module, "prepare_model_snapshot", fake_prepare)
    catalog = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    profile = catalog.profiles["gemma4-12b-unified-fp8"]
    backend = DockerMainModelBackend(
        "/var/run/docker.sock",
        gateway_url="http://gateway:9400",
        cache_dir=str(tmp_path),
    )
    asyncio.run(backend.prepare(catalog, profile))

    assert calls == [
        {
            "model_id": profile.model_id,
            "revision": profile.revision,
            "cache_dir": tmp_path,
        }
    ]


def test_observed_profile_requires_matching_runtime_image(monkeypatch):
    catalog = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    profile = catalog.profiles["gemma4-26b-a4b-fp8"]
    backend = DockerMainModelBackend("/var/run/docker.sock", gateway_url="http://gateway:9400")

    async def fake_container_id(service):
        assert service == catalog.runtime["compose_service"]
        return "container-1"

    async def fake_inspect(_container_id):
        return {
            "Config": {
                "Cmd": list(profile.command),
                "Image": "registry.example.com/wrong@sha256:" + "c" * 64,
            }
        }

    monkeypatch.setattr(backend, "_container_id", fake_container_id)
    monkeypatch.setattr(backend, "_inspect", fake_inspect)

    assert asyncio.run(backend.observed_profile(catalog)) is None


def test_observed_profile_accepts_matching_command_and_runtime_image(monkeypatch):
    catalog = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    profile = catalog.profiles["gemma4-26b-a4b-fp8"]
    backend = DockerMainModelBackend("/var/run/docker.sock", gateway_url="http://gateway:9400")

    async def fake_container_id(service):
        assert service == catalog.runtime["compose_service"]
        return "container-1"

    async def fake_inspect(_container_id):
        return {"Config": {"Cmd": list(profile.command), "Image": profile.image}}

    monkeypatch.setattr(backend, "_container_id", fake_container_id)
    monkeypatch.setattr(backend, "_inspect", fake_inspect)

    assert asyncio.run(backend.observed_profile(catalog)) == profile.profile_id


def test_observed_started_at_returns_container_state_started_at(monkeypatch):
    catalog = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    backend = DockerMainModelBackend("/var/run/docker.sock", gateway_url="http://gateway:9400")

    async def fake_container_id(service):
        assert service == catalog.runtime["compose_service"]
        return "container-1"

    async def fake_inspect(_container_id):
        return {"State": {"Status": "running", "StartedAt": "2026-07-21T00:00:00.123456789Z"}}

    monkeypatch.setattr(backend, "_container_id", fake_container_id)
    monkeypatch.setattr(backend, "_inspect", fake_inspect)

    result = asyncio.run(backend.observed_started_at(catalog))
    assert result == "2026-07-21T00:00:00.123456789Z"


def test_observed_started_at_returns_none_when_container_absent(monkeypatch):
    catalog = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    backend = DockerMainModelBackend("/var/run/docker.sock", gateway_url="http://gateway:9400")

    async def fake_container_id(service):
        return None

    monkeypatch.setattr(backend, "_container_id", fake_container_id)

    assert asyncio.run(backend.observed_started_at(catalog)) is None


class _FakeResponse:
    def __init__(self, payload, *, status_error=False):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            request = httpx.Request("POST", "http://main-llm-vllm:9401/v1/chat/completions")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("server error", request=request, response=response)

    def json(self):
        return self._payload


def _canary_kind(request_json):
    content = request_json["messages"][0]["content"]
    if isinstance(content, str):
        return "text"
    for part in content:
        if part["type"] == "image_url":
            return "image"
        if part["type"] == "input_audio":
            return "audio"
        if part["type"] == "video_url":
            return "video"
    raise AssertionError(f"unrecognized canary payload: {request_json}")


class _FakeAsyncClient:
    """validate()가 만드는 httpx.AsyncClient를 대신해, 실제 컨테이너 없이 어떤 modality
    canary가 실제로 발송됐는지 기록한다. 성공 응답은 항상 "OK" content를 준다."""

    def __init__(self, calls, *, fail_kind=None, status_error_kind=None, public_model="local-main"):
        self.calls = calls
        self._fail_kind = fail_kind
        self._status_error_kind = status_error_kind
        self._public_model = public_model

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url):
        return _FakeResponse({"data": [{"id": self._public_model}]})

    async def post(self, url, json):
        kind = _canary_kind(json)
        self.calls.append(kind)
        if kind == self._status_error_kind:
            return _FakeResponse({}, status_error=True)
        if kind == self._fail_kind:
            return _FakeResponse({"choices": [{"message": {"content": ""}}]})
        return _FakeResponse({"choices": [{"message": {"content": "OK"}}]})


def _patch_healthy_container(monkeypatch, backend):
    async def fake_container_id(service):
        return "container-1"

    async def fake_inspect(_container_id):
        return {"State": {"Health": {"Status": "healthy"}}}

    monkeypatch.setattr(backend, "_container_id", fake_container_id)
    monkeypatch.setattr(backend, "_inspect", fake_inspect)


def test_validate_only_runs_canaries_for_deployed_modalities(monkeypatch):
    # gemma4-26b-a4b-fp8는 deployed_input=[text, image] -- audio/video canary는
    # 아예 발송되면 안 된다(런타임이 지원 안 하는 modality에 요청을 보내는 것 자체가 오류).
    catalog = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    profile = catalog.profiles["gemma4-26b-a4b-fp8"]
    backend = DockerMainModelBackend("/var/run/docker.sock", gateway_url="http://gateway:9400")
    _patch_healthy_container(monkeypatch, backend)

    calls: list[str] = []
    monkeypatch.setattr(backend_module.httpx, "AsyncClient", lambda **_: _FakeAsyncClient(calls))

    asyncio.run(backend.validate(catalog, profile))

    assert calls == ["text", "image"]


def test_validate_runs_all_four_canaries_when_all_deployed(monkeypatch):
    # qwen2.5-omni-7b-thinker는 deployed_input=[text, image, audio, video] 전부를 선언하므로
    # switch-time boot canary도 네 종류 모두 실행해야 한다.
    catalog = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    profile = catalog.profiles["qwen2.5-omni-7b-thinker"]
    backend = DockerMainModelBackend("/var/run/docker.sock", gateway_url="http://gateway:9400")
    _patch_healthy_container(monkeypatch, backend)

    calls: list[str] = []
    monkeypatch.setattr(backend_module.httpx, "AsyncClient", lambda **_: _FakeAsyncClient(calls))

    asyncio.run(backend.validate(catalog, profile))

    assert calls == ["text", "image", "audio", "video"]


def test_validate_raises_when_image_canary_returns_no_content(monkeypatch):
    catalog = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    profile = catalog.profiles["gemma4-26b-a4b-fp8"]
    backend = DockerMainModelBackend("/var/run/docker.sock", gateway_url="http://gateway:9400")
    _patch_healthy_container(monkeypatch, backend)

    calls: list[str] = []
    monkeypatch.setattr(
        backend_module.httpx, "AsyncClient", lambda **_: _FakeAsyncClient(calls, fail_kind="image")
    )

    try:
        asyncio.run(backend.validate(catalog, profile))
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "image canary" in str(exc)


def test_validate_raises_when_image_canary_returns_http_error(monkeypatch):
    # 빈 content(200) 실패 경로와 별개로, runtime이 4xx/5xx를 반환하는 경우도
    # raise_for_status()를 거쳐 같은 방식(예외 -> switch 실패 -> rollback)으로 처리돼야 한다.
    catalog = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    profile = catalog.profiles["gemma4-26b-a4b-fp8"]
    backend = DockerMainModelBackend("/var/run/docker.sock", gateway_url="http://gateway:9400")
    _patch_healthy_container(monkeypatch, backend)

    calls: list[str] = []
    monkeypatch.setattr(
        backend_module.httpx,
        "AsyncClient",
        lambda **_: _FakeAsyncClient(calls, status_error_kind="image"),
    )

    try:
        asyncio.run(backend.validate(catalog, profile))
        raise AssertionError("expected HTTPStatusError")
    except httpx.HTTPStatusError:
        pass
