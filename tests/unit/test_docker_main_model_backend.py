from __future__ import annotations

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
    # The text canary in validate() never sets response_format, so without a
    # dedicated warmup call the xgrammar/Triton constrained-decoding kernel is
    # never JIT-compiled until a real client sends the first
    # response_format=json_schema request post-switch. This locks the warmup
    # payload's shape so it keeps matching the OpenAI-compatible contract that
    # normalize_chat_request_for_runtime/validate_chat_request expect.
    schema = backend_module._STRUCTURED_OUTPUT_WARMUP_SCHEMA
    assert schema["type"] == "json_schema"
    json_schema = schema["json_schema"]
    assert json_schema["strict"] is True
    assert json_schema["schema"]["type"] == "object"
    assert json_schema["schema"]["additionalProperties"] is False
    assert set(json_schema["schema"]["required"]) <= set(json_schema["schema"]["properties"])


def test_validate_calls_structured_output_warmup_and_survives_its_failure(monkeypatch):
    # Regression guard for the fix in ADR-0018's 2026-07-20 update: validate()
    # must actually send the response_format=json_schema warmup request (not
    # just define the schema constant), and a failing warmup must not fail the
    # whole validate() call (it's pure JIT pre-warming, not a correctness gate).
    catalog = load_main_model_catalog(ROOT / "configs/main_model_profiles.yaml")
    profile = catalog.profiles["gemma4-26b-a4b-fp8"]
    backend = DockerMainModelBackend("/var/run/docker.sock")

    async def fake_container_id(service):
        return "container-1"

    async def fake_inspect(_container_id):
        return {"State": {"Health": {"Status": "healthy"}}}

    monkeypatch.setattr(backend, "_container_id", fake_container_id)
    monkeypatch.setattr(backend, "_inspect", fake_inspect)

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == str(catalog.runtime["models_path"]):
            return httpx.Response(200, json={"data": [{"id": catalog.public_model}]})
        payload = json.loads(request.content)
        if payload.get("response_format", {}).get("type") == "json_schema":
            # Simulate the warmup call itself failing (e.g. transient 500) —
            # validate() must swallow this, not propagate it.
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(backend_module.httpx, "AsyncClient", fake_async_client)

    asyncio.run(backend.validate(catalog, profile))  # must not raise

    structured_output_calls = [
        request
        for request in requests
        if request.url.path == str(catalog.runtime["chat_path"])
        and json.loads(request.content).get("response_format", {}).get("type") == "json_schema"
    ]
    assert len(structured_output_calls) == 1
    sent_body = json.loads(structured_output_calls[0].content)
    assert sent_body["response_format"] == backend_module._STRUCTURED_OUTPUT_WARMUP_SCHEMA
