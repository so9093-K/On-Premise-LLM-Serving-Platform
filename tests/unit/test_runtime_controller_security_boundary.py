from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "ops/compose/full-stack.private-network.yaml"


def test_only_runtime_controller_receives_docker_socket() -> None:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = document["services"]
    socket_consumers: dict[str, list[str]] = {}

    for service_name, service in services.items():
        volumes = [str(item) for item in service.get("volumes", [])]
        docker_socket_mounts = [
            item for item in volumes if "/var/run/docker.sock" in item
        ]
        if docker_socket_mounts:
            socket_consumers[str(service_name)] = docker_socket_mounts

    assert socket_consumers == {
        "admin-sidecar": ["/var/run/docker.sock:/var/run/docker.sock:ro"]
    }


def test_runtime_controller_docker_socket_is_not_host_published() -> None:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    controller = document["services"]["admin-sidecar"]

    assert not controller.get("ports")
    assert controller["expose"] == ["8080"]
    assert controller["environment"]["DOCKER_SOCKET"] == "/var/run/docker.sock"
    assert "COMPOSE_PROJECT_NAME" in controller["environment"]["COMPOSE_PROJECT"]
