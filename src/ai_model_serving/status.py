from __future__ import annotations

READY = "ready"
DEGRADED = "degraded"
NOT_READY = "not_ready"
UNKNOWN = "unknown"

PHASE_SERVING = "serving"
PHASE_WAITING_FOR_DEPENDENCIES = "waiting_for_dependencies"

READINESS_STATUSES = (READY, DEGRADED, NOT_READY)
DEPENDENCY_STATUSES = (READY, DEGRADED, NOT_READY, UNKNOWN)
READINESS_PHASES = (PHASE_SERVING, PHASE_WAITING_FOR_DEPENDENCIES)


def is_dependency_ready(status: str) -> bool:
    return status == READY


def overall_readiness(dependency_statuses: list[str]) -> str:
    return READY if all(is_dependency_ready(status) for status in dependency_statuses) else NOT_READY


def readiness_phase(status: str) -> str:
    return PHASE_SERVING if status == READY else PHASE_WAITING_FOR_DEPENDENCIES
