from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..errors import ServiceError
from ..metrics import Metrics
from ..status import NOT_READY, PHASE_SERVING, PHASE_WAITING_FOR_DEPENDENCIES, READY, overall_readiness, readiness_phase

ReadinessEvaluator = Callable[[dict[str, Any]], tuple[str, str | None]]


@dataclass(frozen=True)
class DependencyProbe:
    name: str
    client: Any
    path: str
    headers: Mapping[str, str] | None = None
    evaluator: ReadinessEvaluator | None = None
    required: bool = True


async def collect_readiness(
    *,
    service: str,
    probes: list[DependencyProbe],
    metrics: Metrics | None = None,
) -> dict[str, Any]:
    """Probe dependency readiness and return the platform readiness body."""
    dependencies: list[dict[str, Any]] = []
    for probe in probes:
        endpoint = f"{probe.client.endpoint.base_url.rstrip('/')}/{probe.path.lstrip('/')}"
        message = None
        try:
            probe_json = getattr(probe.client, "probe_json", probe.client.get_json)
            body = await probe_json(probe.path, headers=dict(probe.headers)) if probe.headers is not None else await probe_json(probe.path)
            if probe.evaluator is not None:
                status, message = probe.evaluator(body)
            else:
                status = READY
        except ServiceError as exc:
            status = NOT_READY
            message = f"{exc.code}: {exc.message}"
        if metrics is not None:
            metrics.record_readiness(probe.name, status == READY)
        item: dict[str, Any] = {"name": probe.name, "status": status, "endpoint": endpoint}
        if message:
            item["message"] = message
        dependencies.append(item)

    required_statuses = [str(item["status"]) for item, probe in zip(dependencies, probes) if probe.required]
    overall = overall_readiness(required_statuses) if required_statuses else overall_readiness([])
    not_ready_dependencies = [item["name"] for item in dependencies if item["status"] != "ready"]
    if metrics is not None:
        metrics.record_overall_readiness(overall == READY)
    return {
        "status": overall,
        "service": service,
        "phase": readiness_phase(overall),
        "not_ready_dependencies": not_ready_dependencies,
        "dependencies": dependencies,
    }
