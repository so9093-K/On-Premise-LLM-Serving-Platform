from __future__ import annotations

from typing import Any

from ..errors import ServiceError
from .common import ensure_object, is_int, is_number

EMBEDDING_DIMENSIONS = {768, 512, 256, 128}


def validate_embedding_request(
    payload: Any,
    *,
    expected_model: str,
    request_parameter_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = ensure_object(payload)
    if payload.get("model") != expected_model:
        raise ServiceError("VALIDATION_ERROR", f"model must be {expected_model}.", False, 422)

    policy = request_parameter_policy or {}
    if policy.get("allow_unlisted_parameters") is False:
        allowed = {"model", "input"}.union(set(policy.get("supported_parameters", [])))
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ServiceError("VALIDATION_ERROR", f"Unsupported embedding field(s): {', '.join(unknown)}.", False, 422)

    input_value = payload.get("input")
    valid_input = isinstance(input_value, str) or (
        isinstance(input_value, list) and bool(input_value) and all(isinstance(item, str) for item in input_value)
    )
    if not valid_input:
        raise ServiceError("VALIDATION_ERROR", "input must be a string or non-empty string array.", False, 422)

    supported_dimensions = set(policy.get("dimensions", EMBEDDING_DIMENSIONS))
    dimensions = payload.get("dimensions")
    if dimensions is not None and dimensions not in supported_dimensions:
        raise ServiceError(
            "VALIDATION_ERROR",
            f"dimensions must be one of {sorted(supported_dimensions)}.",
            False,
            422,
        )
    if "encoding_format" in payload and payload["encoding_format"] != "float":
        raise ServiceError("VALIDATION_ERROR", "encoding_format must be float.", False, 422)
    if "truncate_prompt_tokens" in payload:
        value = payload["truncate_prompt_tokens"]
        max_tokens = int(policy.get("max_truncate_prompt_tokens", 2048))
        if not is_int(value) or not (value == -1 or 1 <= value <= max_tokens):
            raise ServiceError("VALIDATION_ERROR", f"truncate_prompt_tokens must be -1 or an integer between 1 and {max_tokens}.", False, 422)
    return payload


def expected_embedding_count(payload: dict[str, Any]) -> int:
    input_value = payload["input"]
    return len(input_value) if isinstance(input_value, list) else 1


def requested_embedding_dimensions(payload: dict[str, Any]) -> int | None:
    dimensions = payload.get("dimensions")
    return int(dimensions) if isinstance(dimensions, int) else None


def validate_embedding_response(
    payload: Any,
    *,
    expected_model: str,
    expected_count: int | None = None,
    expected_dimensions: int | None = None,
) -> dict[str, Any]:
    payload = ensure_object(payload)
    if payload.get("model") != expected_model:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response model must be {expected_model}.", True, 502)
    if payload.get("object") != "list":
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "embedding upstream response object must be list.", True, 502)
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "embedding upstream response data must be a non-empty array.", True, 502)
    if expected_count is not None and len(data) != expected_count:
        raise ServiceError(
            "UPSTREAM_SCHEMA_ERROR",
            f"embedding upstream response data length must match request input count ({expected_count}).",
            True,
            502,
        )
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}] must be an object.", True, 502)
        if item.get("object") != "embedding":
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}].object must be embedding.", True, 502)
        if item.get("index") != index:
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}].index must be {index}.", True, 502)
        vector = item.get("embedding")
        if not isinstance(vector, list):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}].embedding must be an array.", True, 502)
        if not vector:
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}].embedding must be non-empty.", True, 502)
        if expected_dimensions is not None and len(vector) != expected_dimensions:
            raise ServiceError(
                "UPSTREAM_SCHEMA_ERROR",
                f"embedding upstream response data[{index}].embedding dimension must be {expected_dimensions}.",
                True,
                502,
            )
        if not all(is_number(value) for value in vector):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"embedding upstream response data[{index}].embedding must contain numbers only.", True, 502)
    return payload
