from __future__ import annotations

from dataclasses import dataclass
from typing import Any


TECHNICAL_COMPATIBILITY_STATUSES = frozenset({"compatible", "incompatible", "unknown"})
QUALIFICATION_STATUSES = frozenset({"verified", "unverified"})


@dataclass(frozen=True)
class ProfileState:
    compatibility: dict[str, Any]
    qualification: dict[str, Any]


def validate_profile_state(
    profile_id: str,
    compatibility: object,
    qualification: object | None,
) -> ProfileState:
    """Validate the canonical Main Model compatibility and qualification axes."""
    if not isinstance(compatibility, dict):
        raise ValueError(f"profile {profile_id} compatibility must be an object")
    compatibility_out = dict(compatibility)
    compatibility_status = compatibility_out.get("status")
    if compatibility_status not in TECHNICAL_COMPATIBILITY_STATUSES:
        raise ValueError(
            f"profile {profile_id} compatibility.status must be one of "
            f"{sorted(TECHNICAL_COMPATIBILITY_STATUSES)}"
        )

    if not isinstance(qualification, dict):
        raise ValueError(f"profile {profile_id} qualification must be an object")
    qualification_out = dict(qualification)
    qualification_status = qualification_out.get("status")
    if qualification_status not in QUALIFICATION_STATUSES:
        raise ValueError(
            f"profile {profile_id} qualification.status must be one of "
            f"{sorted(QUALIFICATION_STATUSES)}"
        )

    return ProfileState(
        compatibility=compatibility_out,
        qualification=qualification_out,
    )
