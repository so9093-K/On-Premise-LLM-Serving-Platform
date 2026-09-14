from __future__ import annotations

import re
from typing import Any, Mapping

from .errors import ServiceError

_PLAN_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def parse_runtime_transition_request(
    payload: Any,
    *,
    allow_plan_digest: bool,
) -> tuple[str, bool, str | None]:
    if not isinstance(payload, dict):
        raise ServiceError("VALIDATION_ERROR", "Request body must be a JSON object", param="body")

    allowed = {"desired_state", "force"}
    if allow_plan_digest:
        allowed.add("plan_digest")
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ServiceError(
            "VALIDATION_ERROR",
            f"Unsupported field: {unknown[0]}",
            param=unknown[0],
        )

    desired_state = payload.get("desired_state")
    if desired_state not in {"active", "stopped"}:
        raise ServiceError(
            "VALIDATION_ERROR",
            "desired_state must be 'active' or 'stopped'",
            param="desired_state",
        )

    force = payload.get("force", False)
    if not isinstance(force, bool):
        raise ServiceError(
            "VALIDATION_ERROR",
            "force must be a boolean",
            param="force",
        )

    plan_digest = payload.get("plan_digest") if allow_plan_digest else None
    if plan_digest is not None:
        if not isinstance(plan_digest, str) or not _PLAN_DIGEST_RE.fullmatch(plan_digest):
            raise ServiceError(
                "VALIDATION_ERROR",
                "plan_digest must be a lowercase SHA-256 hex digest",
                param="plan_digest",
            )
    return desired_state, force, plan_digest


def project_sidecar_runtime_plan(
    plan: Mapping[str, Any],
    *,
    service_key: str,
    container_to_key: Mapping[str, str],
) -> dict[str, Any]:
    """Sidecar 내부 compose service 이름을 public service_key contract로 투영한다."""

    def public_key(value: object) -> str:
        key = str(value)
        if key == "main":
            return "main"
        return container_to_key.get(key, key)

    budget = plan.get("budget") if isinstance(plan.get("budget"), dict) else {}
    impacts = plan.get("impact") if isinstance(plan.get("impact"), list) else []
    return {
        "service_key": service_key,
        "desired_state": plan.get("desired_state"),
        "force": plan.get("force") is True,
        "current_state": plan.get("current_state"),
        "no_op": plan.get("no_op") is True,
        "admissible": plan.get("admissible") is True,
        "requires_force": plan.get("requires_force") is True,
        "start": [public_key(item) for item in plan.get("start", [])],
        "stop": [public_key(item) for item in plan.get("stop", [])],
        "prerequisites": [public_key(item) for item in plan.get("prerequisites", [])],
        "budget": {
            "before": dict(budget.get("before", {})),
            "after": dict(budget.get("after", {})),
        },
        "impact": [
            {
                "service_key": public_key(item.get("key")),
                "action": item.get("action"),
                "criticality": item.get("criticality"),
            }
            for item in impacts
            if isinstance(item, dict)
        ],
        "reason": plan.get("reason"),
        "plan_digest": plan.get("plan_digest"),
    }
