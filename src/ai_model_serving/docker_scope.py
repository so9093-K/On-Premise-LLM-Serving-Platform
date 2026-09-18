from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


_COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
_COMPOSE_SERVICE_LABEL = "com.docker.compose.service"


def require_compose_project(project: str) -> str:
    """Return a normalized Compose project or fail closed.

    Docker lifecycle calls must never fall back from project+service scope to a
    service-only lookup. Service labels are reused by every Compose project on a
    Docker host, so a missing project would widen authority across deployments.
    """
    normalized = project.strip()
    if not normalized:
        raise RuntimeError(
            "COMPOSE_PROJECT is required before Runtime Controller Docker access"
        )
    return normalized


def compose_container_filter(project: str, service: str | None = None) -> str:
    normalized_project = require_compose_project(project)
    labels = [f"{_COMPOSE_PROJECT_LABEL}={normalized_project}"]
    if service is not None:
        normalized_service = service.strip()
        if not normalized_service:
            raise RuntimeError("Compose service scope must be non-empty")
        labels.append(f"{_COMPOSE_SERVICE_LABEL}={normalized_service}")
    return json.dumps({"label": labels})


def scoped_container_id(
    row: Mapping[str, Any],
    *,
    project: str,
    service: str | None = None,
) -> str:
    """Validate one Docker list row belongs to the requested Compose scope."""
    normalized_project = require_compose_project(project)
    labels = row.get("Labels")
    if not isinstance(labels, Mapping):
        raise RuntimeError("Docker container row is missing Compose labels")
    actual_project = str(labels.get(_COMPOSE_PROJECT_LABEL) or "")
    if actual_project != normalized_project:
        raise RuntimeError(
            "Docker container escaped Compose project scope: "
            f"expected {normalized_project!r}, got {actual_project!r}"
        )
    if service is not None:
        normalized_service = service.strip()
        actual_service = str(labels.get(_COMPOSE_SERVICE_LABEL) or "")
        if actual_service != normalized_service:
            raise RuntimeError(
                "Docker container escaped Compose service scope: "
                f"expected {normalized_service!r}, got {actual_service!r}"
            )
    container_id = str(row.get("Id") or "")
    if not container_id:
        raise RuntimeError("Docker container row is missing Id")
    return container_id


def one_scoped_container_id(
    rows: object,
    *,
    project: str,
    service: str,
) -> str | None:
    if not isinstance(rows, list):
        raise RuntimeError("Docker container listing must be a list")
    ids = [
        scoped_container_id(row, project=project, service=service)
        for row in rows
        if isinstance(row, Mapping)
    ]
    if len(ids) != len(rows):
        raise RuntimeError("Docker container listing contains a non-object row")
    if len(ids) > 1:
        raise RuntimeError(
            f"multiple containers found for scoped service {service!r} "
            f"in Compose project {require_compose_project(project)!r}: {len(ids)}"
        )
    return ids[0] if ids else None
