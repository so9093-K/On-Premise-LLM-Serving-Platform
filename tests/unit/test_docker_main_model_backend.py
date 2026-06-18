from __future__ import annotations

from ai_model_serving.docker_main_model_backend import DockerMainModelBackend


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
