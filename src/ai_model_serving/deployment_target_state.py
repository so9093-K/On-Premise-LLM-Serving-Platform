from __future__ import annotations

from dataclasses import dataclass
from typing import Any


IMPLEMENTATION_STATUSES = frozenset({"planned", "implemented"})
QUALIFICATION_STATUSES = frozenset({"verified", "unverified"})


@dataclass(frozen=True)
class DeploymentTargetState:
    implementation_status: str
    qualification_status: str


def validate_deployment_target_state(
    target_id: str,
    raw: dict[str, Any],
) -> DeploymentTargetState:
    """Validate the canonical Deployment Target implementation and qualification axes."""
    implementation = raw.get("implementation_status")
    qualification = raw.get("qualification_status")
    if not isinstance(implementation, str) or implementation not in IMPLEMENTATION_STATUSES:
        raise ValueError(
            f"deployment target {target_id!r} has invalid implementation_status "
            f"{implementation!r}; allowed: {sorted(IMPLEMENTATION_STATUSES)}"
        )
    if not isinstance(qualification, str) or qualification not in QUALIFICATION_STATUSES:
        raise ValueError(
            f"deployment target {target_id!r} has invalid qualification_status "
            f"{qualification!r}; allowed: {sorted(QUALIFICATION_STATUSES)}"
        )
    if implementation == "planned" and qualification == "verified":
        raise ValueError(
            f"deployment target {target_id!r} cannot be qualification verified "
            "while implementation_status is planned"
        )

    return DeploymentTargetState(
        implementation_status=implementation,
        qualification_status=qualification,
    )
