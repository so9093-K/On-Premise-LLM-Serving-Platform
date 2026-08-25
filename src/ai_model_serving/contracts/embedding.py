from __future__ import annotations

import base64
import binascii
from typing import Any

from ..errors import ServiceError
from .common import ensure_object, is_int, is_number

# 기본 검증값은 현재 일반 embedding runtime이 실제로 보장하는 출력 차원이다.
# 모델 자체의 Matryoshka 가능 여부와 Gateway가 공개하는 런타임 계약은 구분한다.
EMBEDDING_DIMENSIONS = {768}

# 정책이 encoding_format을 선언하지 않으면 float만 받는다(기존 동작).
DEFAULT_ENCODING_FORMATS = ("float",)
# base64 벡터는 little-endian float32 배열이다 -- 차원 = 디코딩 바이트 / 4.
_FLOAT32_BYTES = 4


def _allowed_encoding_formats(policy: dict[str, Any]) -> tuple[str, ...]:
    declared = policy.get("encoding_formats")
    if not isinstance(declared, list) or not declared:
        return DEFAULT_ENCODING_FORMATS
    return tuple(str(item) for item in declared)


def requested_encoding_format(payload: dict[str, Any]) -> str:
    value = payload.get("encoding_format")
    return value if isinstance(value, str) else "float"


def validate_embedding_request(
    payload: Any,
    *,
    expected_model: str,
    request_parameter_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = ensure_object(payload)
    if payload.get("model") != expected_model:
        raise ServiceError("VALIDATION_ERROR", f"model must be {expected_model}.", param="model")

    policy = request_parameter_policy or {}
    if policy.get("allow_unlisted_parameters") is False:
        allowed = {"model", "input"}.union(set(policy.get("supported_parameters", [])))
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ServiceError("VALIDATION_ERROR", f"Unsupported embedding field(s): {', '.join(unknown)}.", param=unknown[0])

    input_value = payload.get("input")
    valid_input = isinstance(input_value, str) or (
        isinstance(input_value, list) and bool(input_value) and all(isinstance(item, str) for item in input_value)
    )
    if not valid_input:
        raise ServiceError("VALIDATION_ERROR", "input must be a string or non-empty string array.", param="input")

    supported_dimensions = set(policy.get("dimensions", EMBEDDING_DIMENSIONS))
    dimensions = payload.get("dimensions")
    if dimensions is not None and dimensions not in supported_dimensions:
        raise ServiceError(
            "VALIDATION_ERROR", f"dimensions must be one of {sorted(supported_dimensions)}.", param="dimensions",
        )
    allowed_formats = _allowed_encoding_formats(policy)
    if "encoding_format" in payload and payload["encoding_format"] not in allowed_formats:
        raise ServiceError(
            "VALIDATION_ERROR",
            f"encoding_format must be one of {', '.join(allowed_formats)}.",
            param="encoding_format",
        )
    if "truncate_prompt_tokens" in payload:
        value = payload["truncate_prompt_tokens"]
        max_tokens = int(policy.get("max_truncate_prompt_tokens", 2048))
        if not is_int(value) or not (value == -1 or 1 <= value <= max_tokens):
            raise ServiceError("VALIDATION_ERROR", f"truncate_prompt_tokens must be -1 or an integer between 1 and {max_tokens}.", param="truncate_prompt_tokens")
    return payload


def expected_embedding_count(payload: dict[str, Any]) -> int:
    input_value = payload["input"]
    return len(input_value) if isinstance(input_value, list) else 1


def requested_embedding_dimensions(payload: dict[str, Any]) -> int | None:
    dimensions = payload.get("dimensions")
    return int(dimensions) if isinstance(dimensions, int) else None


def _validate_base64_vector(value: Any, *, index: int, expected_dimensions: int | None) -> None:
    """base64 벡터가 온전한 float32 배열인지 확인한다.

    float 배열일 때와 같은 두 가지를 본다: 비어 있지 않은가, 그리고 요청한 차원과
    맞는가. 벡터 원소를 실제로 복원하지는 않는다 -- 길이만으로 두 조건이 결정되고,
    응답 하나마다 수천 개의 float을 되살릴 이유가 없다.
    """
    if not isinstance(value, str) or not value:
        raise ServiceError(
            "UPSTREAM_SCHEMA_ERROR",
            f"embedding upstream response data[{index}].embedding must be a base64 string for encoding_format=base64.",
        )
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ServiceError(
            "UPSTREAM_SCHEMA_ERROR",
            f"embedding upstream response data[{index}].embedding is not valid base64.",
        ) from exc
    if not raw or len(raw) % _FLOAT32_BYTES:
        raise ServiceError(
            "UPSTREAM_SCHEMA_ERROR",
            f"embedding upstream response data[{index}].embedding must decode to a non-empty float32 array.",
        )
    dimensions = len(raw) // _FLOAT32_BYTES
    if expected_dimensions is not None and dimensions != expected_dimensions:
        raise ServiceError(
            "UPSTREAM_SCHEMA_ERROR",
            f"embedding upstream response data[{index}].embedding dimension must be {expected_dimensions}.",
        )


def _validate_float_vector(value: Any, *, index: int, expected_dimensions: int | None) -> None:
    if not isinstance(value, list):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}].embedding must be an array.")
    if not value:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}].embedding must be non-empty.")
    if expected_dimensions is not None and len(value) != expected_dimensions:
        raise ServiceError(
            "UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}].embedding dimension must be {expected_dimensions}.",
        )
    if not all(is_number(item) for item in value):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}].embedding must contain numbers only.")


def validate_embedding_response(
    payload: Any,
    *,
    expected_model: str,
    expected_count: int | None = None,
    expected_dimensions: int | None = None,
    encoding_format: str = "float",
) -> dict[str, Any]:
    payload = ensure_object(payload)
    if payload.get("model") != expected_model:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response model must be {expected_model}.")
    if payload.get("object") != "list":
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "embedding upstream response object must be list.")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "embedding upstream response data must be a non-empty array.")
    if expected_count is not None and len(data) != expected_count:
        raise ServiceError(
            "UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data length must match request input count ({expected_count}).",
        )
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}] must be an object.")
        if item.get("object") != "embedding":
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}].object must be embedding.")
        if item.get("index") != index:
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}].index must be {index}.")
        vector = item.get("embedding")
        if encoding_format == "base64":
            _validate_base64_vector(vector, index=index, expected_dimensions=expected_dimensions)
        else:
            _validate_float_vector(vector, index=index, expected_dimensions=expected_dimensions)
    return payload
