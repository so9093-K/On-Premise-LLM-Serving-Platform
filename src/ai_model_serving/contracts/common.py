from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from ..errors import ServiceError

F = TypeVar("F", bound=Callable[..., Any])


def field_param(param: str) -> Callable[[F], F]:
    """Tag field-less VALIDATION_ERRORs raised by a validator with its source field.

    A client should be able to tell a wrong output spec (``response_format``) from a
    wrong input data format (``input_audio``/``image_url``/``video_url``) by reading
    ``error.param`` instead of parsing the message. Precise paths set at the raise
    site (e.g. ``reject_unknown_fields``) win; this only fills the source for the
    remaining raises in a validator. The original exception and traceback are kept
    (``object.__setattr__`` because ServiceError is a frozen dataclass).
    """

    def decorate(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except ServiceError as exc:
                if exc.code == "VALIDATION_ERROR" and exc.param is None:
                    object.__setattr__(exc, "param", param)
                raise

        return wrapper  # type: ignore[return-value]

    return decorate


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
            param=context,
        )
