from __future__ import annotations

import math
from typing import Any


def validate_configuration_value(metadata: dict[str, Any], value: Any) -> None:
    """Validate one value against Configuration Plane metadata constraints."""

    key = str(metadata.get("key", "configuration value"))
    value_type = metadata.get("type")

    if value_type in {"string", "url", "secret"}:
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
    elif value_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
    elif value_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, ValueError):
            finite = False
        if not finite:
            raise ValueError(f"{key} must be a finite number")
    elif value_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be boolean")
    elif value_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"{key} must be an array")
    elif value_type == "enum":
        allowed = metadata.get("enum")
        if not isinstance(allowed, list) or value not in allowed:
            raise ValueError(f"{key} must be one of the declared enum values")
    else:
        raise ValueError(f"{key} has unsupported configuration type {value_type!r}")

    minimum = metadata.get("minimum")
    maximum = metadata.get("maximum")
    if minimum is not None and value < minimum:
        raise ValueError(f"{key} is below metadata minimum {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{key} exceeds metadata maximum {maximum}")
