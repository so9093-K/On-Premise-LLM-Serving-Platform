from __future__ import annotations

from functools import wraps
from typing import Any, Callable, TypeVar

from ..errors import ServiceError

F = TypeVar("F", bound=Callable[..., Any])


def field_param(param: str) -> Callable[[F], F]:
    """필드 정보가 없는 ``VALIDATION_ERROR``에 발생 지점 필드를 보완한다.

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


TOKEN_USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


def normalize_complete_token_usage(
    value: Any,
    *,
    reject_extra_fields: bool = False,
) -> dict[str, int] | None:
    """완전한 OpenAI 호환 토큰 사용량만 정규화해 반환한다.

    ``usage``는 선택 항목일 수 있지만, 제공하는 순간 세 필드가 모두 있어야 한다.
    bool은 Python에서 int의 하위 타입이므로 명시적으로 배제한다. 업스트림은 향후
    세부 usage 필드를 추가할 수 있으므로 기본적으로 필요한 세 필드만 추출한다.
    이미 공개 Risk 응답인 경우에는 ``reject_extra_fields=True``로 JSON Schema의
    ``additionalProperties: false`` 규칙까지 적용한다.
    """
    if not isinstance(value, dict) or not set(TOKEN_USAGE_FIELDS).issubset(value):
        return None
    if reject_extra_fields and set(value) != set(TOKEN_USAGE_FIELDS):
        return None
    normalized: dict[str, int] = {}
    for field in TOKEN_USAGE_FIELDS:
        token_count = value[field]
        if not is_int(token_count) or token_count < 0:
            return None
        normalized[field] = token_count
    return normalized


def ensure_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ServiceError("VALIDATION_ERROR", "request body must be a JSON object.")
    return payload


def reject_unknown_fields(value: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ServiceError(
            "VALIDATION_ERROR", f"{context} contains unsupported field(s): {', '.join(unknown)}.", param=context,
        )
