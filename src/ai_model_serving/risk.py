from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .errors import ServiceError

RISK_LABEL_RE = re.compile(r"<(?P<label>SAFE|UNSAFE-(?P<code>A[12]|I[1-4]))>")
PROMPT_CODES = {"A1", "A2"}
POLICY_CODES = {"I1", "I2", "I3", "I4"}
SYSTEM_CODE_PRIORITY = [
    "GPU_MEMORY_PRESSURE",
    "INFERENCE_TIMEOUT",
    "INFERENCE_QUEUE_TIMEOUT",
    "INFERENCE_ERROR",
    "PARSE_ERROR",
    "TRUNCATED_INPUT",
]
MODEL_CODE_PRIORITY = ["A1", "A2", "I1", "I2", "I3", "I4"]


@dataclass(frozen=True)
class DetectorSpec:
    name: str
    source_model: str
    family: str
    allowed_codes: frozenset[str]


PROMPT_DETECTOR = DetectorSpec(
    name="prompt",
    source_model="risk-prompt",
    family="prompt_attack",
    allowed_codes=frozenset(PROMPT_CODES),
)
SIREN_DETECTOR = DetectorSpec(
    name="siren",
    source_model="risk-siren",
    family="policy_risk",
    allowed_codes=frozenset(POLICY_CODES),
)


def extract_generation_text(response: dict[str, Any], *, expected_model: str | None = None) -> str:
    if expected_model is not None and response.get("model") not in {expected_model, None}:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"Detector response model must be {expected_model}.", True, 502)
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ServiceError("PARSE_ERROR", "Detector response does not contain choices.", True, 502)
    first = choices[0]
    if not isinstance(first, dict):
        raise ServiceError("PARSE_ERROR", "Detector response choice is invalid.", True, 502)
    finish_reason = first.get("finish_reason")
    if finish_reason is not None and finish_reason not in {"stop", "length"}:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "Detector response finish_reason is not supported.", True, 502)
    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(first.get("text"), str):
        return first["text"]
    raise ServiceError("PARSE_ERROR", "Detector response does not contain text content.", True, 502)


def parse_detector_label(text: str, detector: DetectorSpec) -> dict[str, Any]:
    stripped = text.strip()
    matches = list(RISK_LABEL_RE.finditer(stripped))
    if len(matches) != 1 or matches[0].group(0) != stripped:
        raise ServiceError("PARSE_ERROR", "Detector output must be exactly one supported risk label.", True, 502)
    match = matches[0]
    code = match.group("code")
    label = match.group(0)
    if code is None:
        return {
            "code": None,
            "family": detector.family,
            "detected": False,
            "confidence": None,
            "source_model": detector.source_model,
            "label": label,
        }
    if code not in detector.allowed_codes:
        raise ServiceError("PARSE_ERROR", f"Detector returned code outside family: {code}", True, 502)
    return {
        "code": code,
        "family": detector.family,
        "detected": True,
        "confidence": None,
        "source_model": detector.source_model,
        "label": label,
    }


def system_signal(code: str, message: str, source: str, retryable: bool = True) -> dict[str, Any]:
    return {"code": code, "detected": True, "retryable": retryable, "message": message, "source": source}


def assessment_response(
    *,
    categories: list[dict[str, Any]],
    system_signals: list[dict[str, Any]],
    status: str,
    message: str,
) -> dict[str, Any]:
    model_risk_detected = any(item.get("detected") for item in categories)
    system_signal_detected = any(item.get("detected") for item in system_signals)
    strongest_code = choose_strongest_code(categories, system_signals)
    return {
        "assessment_id": f"risk_{uuid4().hex}",
        "status": status,
        "risk_detected": model_risk_detected,
        "attention_required": model_risk_detected or system_signal_detected,
        "model_risk_detected": model_risk_detected,
        "system_signal_detected": system_signal_detected,
        "assessment_complete": status == "completed",
        "strongest_code": strongest_code,
        "message": message,
        "categories": categories,
        "system_signals": system_signals,
    }


def choose_strongest_code(categories: list[dict[str, Any]], system_signals: list[dict[str, Any]]) -> str | None:
    detected_codes = {item["code"] for item in categories if item.get("detected") and item.get("code")}
    for code in MODEL_CODE_PRIORITY:
        if code in detected_codes:
            return code
    detected_system = {item["code"] for item in system_signals if item.get("detected")}
    for code in SYSTEM_CODE_PRIORITY:
        if code in detected_system:
            return code
    return None
