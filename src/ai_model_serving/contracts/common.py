from __future__ import annotations

from typing import Any

from ..errors import ServiceError


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def ensure_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ServiceError("VALIDATION_ERROR", "request body must be a JSON object.", False, 422)
    return payload


def reject_unknown_fields(value: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ServiceError(
            "VALIDATION_ERROR",
            f"{context} contains unsupported field(s): {', '.join(unknown)}.",
            False,
            422,
        )
