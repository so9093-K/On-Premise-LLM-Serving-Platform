from __future__ import annotations

import json

import pytest

from ai_model_serving.docker_scope import (
    compose_container_filter,
    one_scoped_container_id,
    scoped_container_id,
)


def _row(*, project: str = "platform", service: str = "embedding-vllm", container_id: str = "abc") -> dict:
    return {
        "Id": container_id,
        "Labels": {
            "com.docker.compose.project": project,
            "com.docker.compose.service": service,
        },
    }


def test_compose_container_filter_always_contains_project_and_service() -> None:
    value = json.loads(compose_container_filter("platform", "embedding-vllm"))

    assert value == {
        "label": [
            "com.docker.compose.project=platform",
            "com.docker.compose.service=embedding-vllm",
        ]
    }


def test_compose_container_filter_rejects_missing_project() -> None:
    with pytest.raises(RuntimeError, match="COMPOSE_PROJECT is required"):
        compose_container_filter("", "embedding-vllm")


def test_scoped_container_id_rejects_cross_project_result() -> None:
    with pytest.raises(RuntimeError, match="escaped Compose project scope"):
        scoped_container_id(
            _row(project="other"),
            project="platform",
            service="embedding-vllm",
        )


def test_scoped_container_id_rejects_cross_service_result() -> None:
    with pytest.raises(RuntimeError, match="escaped Compose service scope"):
        scoped_container_id(
            _row(service="risk-prompt-vllm"),
            project="platform",
            service="embedding-vllm",
        )


def test_one_scoped_container_id_rejects_duplicate_service_instances() -> None:
    with pytest.raises(RuntimeError, match="multiple containers found"):
        one_scoped_container_id(
            [_row(container_id="one"), _row(container_id="two")],
            project="platform",
            service="embedding-vllm",
        )


def test_one_scoped_container_id_returns_only_scoped_match() -> None:
    assert (
        one_scoped_container_id(
            [_row(container_id="one")],
            project="platform",
            service="embedding-vllm",
        )
        == "one"
    )
