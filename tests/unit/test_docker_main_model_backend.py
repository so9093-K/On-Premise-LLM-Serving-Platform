"""DockerMainModelBackend의 결정론적 단위 테스트.

Docker daemon을 띄우지 않고 inspect·HTTP 요청을 fake로 대체한다. 컨테이너 재생성
정책과 Profile 기반 health·media canary 계약을 보호하므로 기본 `make test`에 포함한다.
"""

from __future__ import annotations

import asyncio

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
