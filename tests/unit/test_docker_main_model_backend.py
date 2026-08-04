"""DockerMainModelBackend를 검증한다: 컨테이너 재생성 시 허용 목록 필드만
복사하는지, 프로필 전환 시 캐시/이미지 정합성, structured-output/tool-calling
warmup이 실제로 나가고 그 실패가 validate() 전체를 막지 않는지. `docker` 마커가
붙어 있어 `make test`(마커 not docker) 기본 실행에는 포함되지 않는다."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.docker

import asyncio
import json

import httpx

import ai_model_serving.docker_main_model_backend as backend_module
from ai_model_serving.docker_main_model_backend import DockerMainModelBackend
from ai_model_serving.main_model_control import load_main_model_catalog

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
    backend = DockerMainModelBackend("/var/run/docker.sock")

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
    backend = DockerMainModelBackend("/var/run/docker.sock")

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
    backend = DockerMainModelBackend("/var/run/docker.sock")

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
    backend = DockerMainModelBackend("/var/run/docker.sock")

    async def fake_container_id(service):
        return None

    monkeypatch.setattr(backend, "_container_id", fake_container_id)

    assert asyncio.run(backend.observed_started_at(catalog)) is None


def test_structured_output_warmup_schema_is_a_valid_strict_json_schema_response_format():
    # validate()의 text canary는 response_format을 절대 설정하지 않는다, 그래서
    # 전용 warmup 호출이 없으면 xgrammar/Triton constrained-decoding 커널은
    # 전환 후 실제 클라이언트가 첫 response_format=json_schema 요청을 보낼
    # 때까지 절대 JIT-컴파일되지 않는다. warmup payload의 모양을
    # normalize_chat_request_for_runtime/validate_chat_request가 기대하는
    # OpenAI 호환 계약과 계속 맞도록 여기서 고정한다.
    schema = backend_module._STRUCTURED_OUTPUT_WARMUP_SCHEMA
    assert schema["type"] == "json_schema"
    json_schema = schema["json_schema"]
    assert json_schema["strict"] is True
    assert json_schema["schema"]["type"] == "object"
    assert json_schema["schema"]["additionalProperties"] is False
    assert set(json_schema["schema"]["required"]) <= set(json_schema["schema"]["properties"])


def test_validate_calls_structured_output_warmup_and_survives_its_failure(monkeypatch):
    # ADR-0018의 2026-07-20 업데이트 수정에 대한 회귀 가드: validate()는
    # (스키마 상수만 정의하는 게 아니라) response_format=json_schema warmup
    # 요청을 실제로 보내야 하고, warmup 실패가 validate() 호출 전체를 실패시키면
    # 안 된다(순수 JIT 사전 예열일 뿐 정확성 게이트가 아니다). 2026-07-21
    # raise_for_status() 수정도 함께 가드한다: 4xx/5xx warmup 응답은 감지해서
    # 로그로 남겨야 하고, 조용히 성공으로 처리되면 안 된다(httpx는
    # raise_for_status()를 명시적으로 호출하지 않으면 에러 상태 코드에도
    # 예외를 던지지 않는다).
    catalog = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    profile = catalog.profiles["gemma4-26b-a4b-fp8"]
    backend = DockerMainModelBackend("/var/run/docker.sock")

    async def fake_container_id(service):
        return "container-1"

    async def fake_inspect(_container_id):
        return {"State": {"Health": {"Status": "healthy"}}}

    monkeypatch.setattr(backend, "_container_id", fake_container_id)
    monkeypatch.setattr(backend, "_inspect", fake_inspect)

    warnings: list[str] = []
    monkeypatch.setattr(
        backend_module._logger,
        "warning",
        lambda msg, *args: warnings.append(msg % args if args else msg),
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == str(catalog.runtime["models_path"]):
            return httpx.Response(200, json={"data": [{"id": catalog.public_model}]})
        payload = json.loads(request.content)
        if payload.get("response_format", {}).get("type") == "json_schema":
            # warmup 호출 자체가 실패하는 상황(예: 일시적 500)을 시뮬레이션한다 —
            # validate()는 이걸 삼켜야 하고 전파하면 안 된다.
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(backend_module.httpx, "AsyncClient", fake_async_client)

    asyncio.run(backend.validate(catalog, profile))  # 예외가 나면 안 된다

    structured_output_calls = [
        request
        for request in requests
        if request.url.path == str(catalog.runtime["chat_path"])
        and json.loads(request.content).get("response_format", {}).get("type") == "json_schema"
    ]
    assert len(structured_output_calls) == 1
    sent_body = json.loads(structured_output_calls[0].content)
    assert sent_body["response_format"] == backend_module._STRUCTURED_OUTPUT_WARMUP_SCHEMA
    assert any("structured-output warmup canary failed" in message for message in warnings)


def test_tool_call_warmup_tools_shape_is_a_valid_function_tool():
    # --enable-auto-tool-choice --tool-call-parser gemma4도 xgrammar
    # constrained decoding을 거치므로, 예열 안 된 엔진에서
    # response_format=json_schema와 같은 Triton JIT 비용을 겪을 수 있다. 이
    # 모양을 OpenAI 호환 `tools` 계약(contracts/chat_tools.py)과 계속 맞도록
    # 여기서 고정한다.
    tools = backend_module._TOOL_CALL_WARMUP_TOOLS
    assert len(tools) == 1
    tool = tools[0]
    assert tool["type"] == "function"
    function = tool["function"]
    assert isinstance(function["name"], str) and function["name"]
    assert function["parameters"]["type"] == "object"
    assert set(function["parameters"]["required"]) <= set(function["parameters"]["properties"])


def test_validate_calls_tool_calling_warmup_and_survives_its_failure(monkeypatch):
    # 위 structured-output warmup 테스트와 같은 회귀 형태다: validate()는
    # tool_choice를 강제한 warmup 요청을 실제로 보내야 하고, 실패해도 validate()
    # 전체가 실패하면 안 된다. raise_for_status() 수정도 함께 가드한다(왜
    # 중요한지는 위 structured-output 테스트 참고).
    catalog = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    profile = catalog.profiles["gemma4-26b-a4b-fp8"]
    backend = DockerMainModelBackend("/var/run/docker.sock")

    async def fake_container_id(service):
        return "container-1"

    async def fake_inspect(_container_id):
        return {"State": {"Health": {"Status": "healthy"}}}

    monkeypatch.setattr(backend, "_container_id", fake_container_id)
    monkeypatch.setattr(backend, "_inspect", fake_inspect)

    warnings: list[str] = []
    monkeypatch.setattr(
        backend_module._logger,
        "warning",
        lambda msg, *args: warnings.append(msg % args if args else msg),
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == str(catalog.runtime["models_path"]):
            return httpx.Response(200, json={"data": [{"id": catalog.public_model}]})
        payload = json.loads(request.content)
        if "tool_choice" in payload:
            # tool-calling warmup 자체가 실패하는 상황을 시뮬레이션한다 —
            # structured-output warmup과 마찬가지로 validate()가 이것도 삼켜야 한다.
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(backend_module.httpx, "AsyncClient", fake_async_client)

    asyncio.run(backend.validate(catalog, profile))  # 예외가 나면 안 된다

    tool_call_warmup_calls = [
        request
        for request in requests
        if request.url.path == str(catalog.runtime["chat_path"])
        and "tool_choice" in json.loads(request.content)
    ]
    assert len(tool_call_warmup_calls) == 1
    sent_body = json.loads(tool_call_warmup_calls[0].content)
    assert sent_body["tools"] == backend_module._TOOL_CALL_WARMUP_TOOLS
    assert sent_body["tool_choice"] == {"type": "function", "function": {"name": "warmup"}}
    assert any("tool-calling warmup canary failed" in message for message in warnings)
