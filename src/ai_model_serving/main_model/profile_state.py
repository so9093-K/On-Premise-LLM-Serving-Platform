from __future__ import annotations

from dataclasses import dataclass
from typing import Any


TECHNICAL_COMPATIBILITY_STATUSES = frozenset({"compatible", "incompatible", "unknown"})
QUALIFICATION_STATUSES = frozenset({"verified", "unverified"})
LEGACY_COMPATIBILITY_STATUSES = frozenset(
    {"verified", "likely", "unverified", "incompatible", "unknown"}
)

_LEGACY_STATE_MAP: dict[str, tuple[str, str]] = {
    "verified": ("compatible", "verified"),
    "likely": ("unknown", "unverified"),
    "unverified": ("compatible", "unverified"),
    "incompatible": ("incompatible", "unverified"),
    "unknown": ("unknown", "unverified"),
}


@dataclass(frozen=True)
class NormalizedProfileState:
    compatibility: dict[str, Any]
    qualification: dict[str, Any]
    legacy_status: str


def legacy_compatibility_status(compatibility_status: str, qualification_status: str) -> str:
    """Project split state back to the legacy Admin API compatibility status."""
    if compatibility_status == "incompatible":
        return "incompatible"
    if compatibility_status == "unknown":
        return "unknown"
    if compatibility_status == "compatible" and qualification_status == "verified":
        return "verified"
    if compatibility_status == "compatible" and qualification_status == "unverified":
        return "unverified"
    raise ValueError(
        "invalid main model state combination: "
        f"compatibility={compatibility_status!r}, qualification={qualification_status!r}"
    )


def normalize_profile_state(
    profile_id: str,
    compatibility: object,
    qualification: object | None,
) -> NormalizedProfileState:
    """Normalize canonical or legacy Main Model profile state.

    Canonical configuration uses two independent axes:

    - compatibility.status: compatible | incompatible | unknown
    - qualification.status: verified | unverified

    Legacy catalogs used one overloaded compatibility.status value. They remain
    readable during migration, including catalogs that already use qualification
    for additional evidence/budget metadata but do not yet declare a status.
    """
    if not isinstance(compatibility, dict):
        raise ValueError(f"profile {profile_id} compatibility must be an object")

    compatibility_out = dict(compatibility)
    raw_compatibility_status = compatibility_out.get("status")
    if not isinstance(raw_compatibility_status, str):
        raise ValueError(f"profile {profile_id} compatibility.status is required")

    if qualification is None:
        qualification_out: dict[str, Any] = {}
    elif isinstance(qualification, dict):
        qualification_out = dict(qualification)
    else:
        raise ValueError(f"profile {profile_id} qualification must be an object")

    qualification_status = qualification_out.get("status")

    if raw_compatibility_status in TECHNICAL_COMPATIBILITY_STATUSES:
        if qualification_status not in QUALIFICATION_STATUSES:
            raise ValueError(
                f"profile {profile_id} canonical compatibility.status "
                f"{raw_compatibility_status!r} requires qualification.status in "
                f"{sorted(QUALIFICATION_STATUSES)}"
            )
        compatibility_out["status"] = raw_compatibility_status
        qualification_out["status"] = str(qualification_status)
        legacy_status = legacy_compatibility_status(
            raw_compatibility_status,
            str(qualification_status),
        )
        return NormalizedProfileState(
            compatibility=compatibility_out,
            qualification=qualification_out,
            legacy_status=legacy_status,
        )

    if raw_compatibility_status not in LEGACY_COMPATIBILITY_STATUSES:
        allowed = sorted(TECHNICAL_COMPATIBILITY_STATUSES | LEGACY_COMPATIBILITY_STATUSES)
        raise ValueError(
            f"profile {profile_id} has invalid compatibility.status "
            f"{raw_compatibility_status!r}; allowed during migration: {allowed}"
        )

    if qualification_status is not None:
        raise ValueError(
            f"profile {profile_id} must not combine legacy compatibility.status "
            f"{raw_compatibility_status!r} with qualification.status"
        )

    technical_status, normalized_qualification = _LEGACY_STATE_MAP[raw_compatibility_status]
    compatibility_out["status"] = technical_status
    qualification_out["status"] = normalized_qualification
    return NormalizedProfileState(
        compatibility=compatibility_out,
        qualification=qualification_out,
        legacy_status=raw_compatibility_status,
    )


def public_compatibility_projection(state: NormalizedProfileState) -> dict[str, Any]:
    """Return the backwards-compatible Admin API compatibility object.

    compatibility.status stays on the legacy vocabulary for existing consumers.
    technical_status exposes the canonical axis during the compatibility period.
    """
    return {
        **state.compatibility,
        "status": state.legacy_status,
        "technical_status": state.compatibility["status"],
    }
