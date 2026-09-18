from __future__ import annotations

from dataclasses import dataclass
from typing import Any


IMPLEMENTATION_STATUSES = frozenset({"planned", "implemented"})
QUALIFICATION_STATUSES = frozenset({"verified", "unverified"})
LEGACY_VALIDATION_STATUSES = frozenset(
    {"verified", "implemented", "planned", "unvalidated"}
)

_LEGACY_STATE_MAP: dict[str, tuple[str, str]] = {
    "verified": ("implemented", "verified"),
    "implemented": ("implemented", "unverified"),
    "planned": ("planned", "unverified"),
    # Legacy "unvalidated" was not startable. Mapping it to planned preserves
    # that fail-closed behavior while the old vocabulary is retired.
    "unvalidated": ("planned", "unverified"),
}


@dataclass(frozen=True)
class NormalizedDeploymentTargetState:
    implementation_status: str
    qualification_status: str
    legacy_validation_status: str


def legacy_validation_status(
    implementation_status: str,
    qualification_status: str,
) -> str:
    """Project canonical target state back to the legacy validation_status field."""
    if implementation_status == "implemented":
        return "verified" if qualification_status == "verified" else "implemented"
    if implementation_status == "planned" and qualification_status == "unverified":
        return "planned"
    raise ValueError(
        "invalid deployment target state combination: "
        f"implementation_status={implementation_status!r}, "
        f"qualification_status={qualification_status!r}"
    )


def normalize_deployment_target_state(
    target_id: str,
    raw: dict[str, Any],
) -> NormalizedDeploymentTargetState:
    """Normalize canonical or legacy Deployment Target state.

    Canonical configuration uses two independent axes:
      - implementation_status: planned | implemented
      - qualification_status: verified | unverified

    The legacy validation_status field remains readable during migration, but
    canonical and legacy declarations must never be mixed.
    """
    implementation = raw.get("implementation_status")
    qualification = raw.get("qualification_status")
    legacy = raw.get("validation_status")

    canonical_declared = implementation is not None or qualification is not None
    if canonical_declared:
        if legacy is not None:
            raise ValueError(
                f"deployment target {target_id!r} must not combine canonical state "
                "with legacy validation_status"
            )
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
        projected = legacy_validation_status(str(implementation), str(qualification))
        return NormalizedDeploymentTargetState(
            implementation_status=str(implementation),
            qualification_status=str(qualification),
            legacy_validation_status=projected,
        )

    if not isinstance(legacy, str) or legacy not in LEGACY_VALIDATION_STATUSES:
        raise ValueError(
            f"deployment target {target_id!r} has invalid validation_status {legacy!r}; "
            f"allowed during migration: {sorted(LEGACY_VALIDATION_STATUSES)}"
        )

    implementation_status, qualification_status = _LEGACY_STATE_MAP[str(legacy)]
    return NormalizedDeploymentTargetState(
        implementation_status=implementation_status,
        qualification_status=qualification_status,
        legacy_validation_status=str(legacy),
    )
