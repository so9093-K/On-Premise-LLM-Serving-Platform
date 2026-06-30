from __future__ import annotations

from typing import Any

from ..errors import ServiceError
from .common import ensure_object

MAX_RISK_PROMPT_LENGTH = 20_000
FORBIDDEN_RISK_RESPONSE_FIELDS = {
    "allow",
    "review",
    "block",
    "decision",
    "action",
    "safe_to_send",
    "final_decision",
    "final_decision_owner",
    "policy_overrides",
}
RISK_RESPONSE_STATUS = {"completed", "partial", "failed"}
MODEL_RISK_CODES = {"A1", "A2", "I1", "I2", "I3", "I4", "D1", "D2", "D3", "D4", "D5"}
DATA_EXPOSURE_CODES = {"D1", "D2", "D3", "D4", "D5"}
SYSTEM_RISK_CODES = {
    "GPU_MEMORY_PRESSURE",
    "INFERENCE_TIMEOUT",
    "INFERENCE_QUEUE_TIMEOUT",
    "INFERENCE_ERROR",
    "PARSE_ERROR",
    "TRUNCATED_INPUT",
}
RISK_RESPONSE_REQUIRED_FIELDS = {
    "assessment_id",
    "status",
    "risk_detected",
    "attention_required",
    "model_risk_detected",
    "system_signal_detected",
    "assessment_complete",
    "strongest_code",
    "message",
    "categories",
    "system_signals",
}


def read_risk_prompt(payload: Any) -> str:
    payload = ensure_object(payload)
    if set(payload) != {"prompt"}:
        raise ServiceError("VALIDATION_ERROR", "request body must contain only prompt.", False, 422, param="prompt")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ServiceError("VALIDATION_ERROR", "prompt is required.", False, 422, param="prompt")
    if len(prompt) > MAX_RISK_PROMPT_LENGTH:
        raise ServiceError("VALIDATION_ERROR", "prompt must be 20000 characters or fewer.", False, 422, param="prompt")
    return prompt


def _validate_risk_category(category: Any, *, index: int) -> bool:
    if not isinstance(category, dict):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}] must be an object.", True, 502)
    required = {"code", "family", "detected", "confidence"}
    if not required.issubset(category):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}] is missing required fields.", True, 502)
    if not isinstance(category.get("detected"), bool):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}].detected must be boolean.", True, 502)
    code = category.get("code")
    family = category.get("family")
    label = category.get("label")

    if code is None:
        # Safe category: detected must be False
        if category["detected"] is not False:
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}] safe category is inconsistent.", True, 502)
        # For prompt_attack safe categories, label must be "<SAFE>"
        # For data_exposure safe categories, label may be None
        if family == "prompt_attack" and label != "<SAFE>":
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}] prompt safe label must be <SAFE>.", True, 502)
        if family not in {"prompt_attack", "policy_risk", "data_exposure"}:
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}] family is not supported.", True, 502)
    elif code in {"A1", "A2"}:
        if family != "prompt_attack" or label != f"<UNSAFE-{code}>":
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}] prompt risk code is inconsistent.", True, 502)
    elif code in {"I1", "I2", "I3", "I4"}:
        if family != "policy_risk" or label != f"<UNSAFE-{code}>":
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}] policy risk code is inconsistent.", True, 502)
    elif code in DATA_EXPOSURE_CODES:
        if family != "data_exposure":
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}] data exposure code must have family=data_exposure.", True, 502)
        # label is an entity label (e.g. "KR_RRN", "EMAIL_ADDRESS") — must be non-empty string when detected
        if category["detected"] and (not isinstance(label, str) or not label):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}] detected data_exposure category must have a non-empty label.", True, 502)
        # span_count is optional: None or int >= 0
        span_count = category.get("span_count")
        if span_count is not None and (not isinstance(span_count, int) or span_count < 0):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}].span_count must be a non-negative integer or null.", True, 502)
    else:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}].code is not supported.", True, 502)

    # source_model validation: required field for all detector-produced categories (vLLM or local)
    # For safe prompt categories the field exists but may vary; for data_exposure it must be present and non-empty.
    if "source_model" in category:
        sm = category["source_model"]
        if sm is not None and (not isinstance(sm, str) or not sm):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}].source_model must be a non-empty string or null.", True, 502)
    if code in DATA_EXPOSURE_CODES:
        # source_model is required for data_exposure categories
        if "source_model" not in category or not isinstance(category.get("source_model"), str) or not category["source_model"]:
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}].source_model must be a non-empty string for data_exposure.", True, 502)
    elif code is not None:
        # For A/I codes: source_model must be present and non-empty
        if not isinstance(category.get("source_model"), str) or not category["source_model"]:
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response categories[{index}].source_model must be a non-empty string.", True, 502)

    return bool(category["detected"]) and code is not None


def _validate_risk_system_signal(signal: Any, *, index: int) -> bool:
    if not isinstance(signal, dict):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response system_signals[{index}] must be an object.", True, 502)
    required = {"code", "detected", "retryable"}
    if not required.issubset(signal):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response system_signals[{index}] is missing required fields.", True, 502)
    if signal.get("code") not in SYSTEM_RISK_CODES:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response system_signals[{index}].code is not supported.", True, 502)
    if not isinstance(signal.get("detected"), bool):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response system_signals[{index}].detected must be boolean.", True, 502)
    if not isinstance(signal.get("retryable"), bool):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response system_signals[{index}].retryable must be boolean.", True, 502)
    return bool(signal["detected"])


def validate_risk_response(payload: Any) -> dict[str, Any]:
    """Validate the internal Risk Adapter response before exposing it at Gateway.

    This mirrors the signal-only contract without adding jsonschema as a runtime
    dependency. It prevents an internal service or future code path from leaking
    final policy-decision fields through the public Gateway.
    """
    payload = ensure_object(payload)
    forbidden = sorted(FORBIDDEN_RISK_RESPONSE_FIELDS.intersection(payload))
    if forbidden:
        names = ", ".join(forbidden)
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response contains forbidden policy field(s): {names}.", True, 502)
    missing = sorted(RISK_RESPONSE_REQUIRED_FIELDS.difference(payload))
    if missing:
        names = ", ".join(missing)
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response is missing required field(s): {names}.", True, 502)

    if not isinstance(payload.get("assessment_id"), str) or not payload["assessment_id"].startswith("risk_"):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "risk response assessment_id must start with risk_.", True, 502)
    status = payload.get("status")
    if status not in RISK_RESPONSE_STATUS:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "risk response status is not supported.", True, 502)
    if not isinstance(payload.get("message"), str):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "risk response message must be a string.", True, 502)

    categories = payload.get("categories")
    system_signals = payload.get("system_signals")
    if not isinstance(categories, list) or not isinstance(system_signals, list):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "risk response categories and system_signals must be arrays.", True, 502)

    model_category_detections = [
        _validate_risk_category(item, index=i) for i, item in enumerate(categories)
    ]
    system_signal_detections = [
        _validate_risk_system_signal(item, index=i) for i, item in enumerate(system_signals)
    ]
    model_risk_detected = any(model_category_detections)
    system_signal_detected = any(system_signal_detections)

    bool_fields = [
        "risk_detected",
        "attention_required",
        "model_risk_detected",
        "system_signal_detected",
        "assessment_complete",
    ]
    for field in bool_fields:
        if not isinstance(payload.get(field), bool):
            raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"risk response {field} must be boolean.", True, 502)

    if payload["risk_detected"] != model_risk_detected or payload["model_risk_detected"] != model_risk_detected:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "risk response model risk booleans are inconsistent.", True, 502)
    if payload["system_signal_detected"] != system_signal_detected:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "risk response system signal boolean is inconsistent.", True, 502)
    if payload["attention_required"] != (model_risk_detected or system_signal_detected):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "risk response attention_required is inconsistent.", True, 502)
    if payload["assessment_complete"] != (status == "completed"):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "risk response assessment_complete is inconsistent with status.", True, 502)

    strongest_code = payload.get("strongest_code")
    if strongest_code is not None and strongest_code not in MODEL_RISK_CODES | SYSTEM_RISK_CODES:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "risk response strongest_code is not supported.", True, 502)
    if strongest_code is not None and not (model_risk_detected or system_signal_detected):
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "risk response strongest_code requires a detected model or system signal.", True, 502)

    return payload
