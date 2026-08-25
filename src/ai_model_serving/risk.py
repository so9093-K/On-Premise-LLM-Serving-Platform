from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .contracts.common import TOKEN_USAGE_FIELDS, normalize_complete_token_usage
from .errors import ServiceError

RISK_LABEL_RE = re.compile(r"<(?P<label>SAFE|UNSAFE-(?P<code>A[12]))>")
SYSTEM_CODE_PRIORITY = [
    "INFERENCE_TIMEOUT",
    "INFERENCE_QUEUE_TIMEOUT",
    "INFERENCE_ERROR",
    "PARSE_ERROR",
    "TRUNCATED_INPUT",
]
# D4(Secret/Credential)는 가장 강력한 data exposure 신호이며, prompt attack보다
# 우선순위가 높다. D1, D2와 D5는 prompt attack(A1, A2) 다음 우선순위를 따른다.
MODEL_CODE_PRIORITY = ["D4", "A1", "A2", "D1", "D2", "D5"]


@dataclass(frozen=True)
class DetectorSpec:
    name: str
    source_model: str
    family: str
    allowed_codes: frozenset[str]
    route: str = ""
    service_key: str = ""
    max_output_tokens: int = 1
    temperature: float = 0


def extract_generation_text(response: dict[str, Any], *, expected_model: str | None = None) -> str:
    if expected_model is not None and response.get("model") not in {expected_model, None}:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", f"Detector response model must be {expected_model}.")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ServiceError("PARSE_ERROR", "Detector response does not contain choices.")
    first = choices[0]
    if not isinstance(first, dict):
        raise ServiceError("PARSE_ERROR", "Detector response choice is invalid.")
    finish_reason = first.get("finish_reason")
    if finish_reason is not None and finish_reason not in {"stop", "length"}:
        raise ServiceError("UPSTREAM_SCHEMA_ERROR", "Detector response finish_reason is not supported.")
    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(first.get("text"), str):
        return first["text"]
    raise ServiceError("PARSE_ERROR", "Detector response does not contain text content.")


def parse_detector_label(
    text: str,
    detector: DetectorSpec,
    *,
    top_probabilities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    stripped = text.strip()
    matches = list(RISK_LABEL_RE.finditer(stripped))
    if len(matches) != 1 or matches[0].group(0) != stripped:
        raise ServiceError("PARSE_ERROR", "Detector output must be exactly one supported risk label.")
    match = matches[0]
    code = match.group("code")
    label = match.group(0)
    if code is None:
        category = {
            "code": None,
            "family": detector.family,
            "detected": False,
            "confidence": None,
            "source_model": detector.source_model,
            "label": label,
        }
    else:
        if code not in detector.allowed_codes:
            raise ServiceError("PARSE_ERROR", f"Detector returned code outside family: {code}")
        category = {
            "code": code,
            "family": detector.family,
            "detected": True,
            "confidence": None,
            "source_model": detector.source_model,
            "label": label,
        }
    if top_probabilities is not None:
        category["top_probabilities"] = top_probabilities
    return category


def first_token_top_probabilities(response: dict[str, Any]) -> list[dict[str, Any]] | None:
    """vLLM 첫 생성 토큰의 top logprob를 사람이 읽는 확률로 투영한다.

    진단 정보가 없는 응답은 기존 risk 판정에 영향을 주지 않는다. top 후보 일부만
    반환되는 OpenAI-compatible API 특성상, 호출자가 받지 못한 라벨을 0으로 만들지 않는다.
    """
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    logprobs = choices[0].get("logprobs")
    if not isinstance(logprobs, dict):
        return None
    content = logprobs.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        return None
    candidates = content[0].get("top_logprobs")
    if not isinstance(candidates, list):
        return None

    probabilities: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        token = candidate.get("token")
        logprob = candidate.get("logprob")
        if not isinstance(token, str) or not isinstance(logprob, (int, float)) or isinstance(logprob, bool):
            continue
        if not math.isfinite(logprob):
            continue
        probability = math.exp(logprob)
        if 0 <= probability <= 1:
            probabilities.append({"token": token, "probability": probability})
    return probabilities or None


def system_signal(code: str, message: str, source: str, retryable: bool = True) -> dict[str, Any]:
    return {"code": code, "detected": True, "retryable": retryable, "message": message, "source": source}


def assessment_response(
    *,
    categories: list[dict[str, Any]],
    system_signals: list[dict[str, Any]],
    status: str,
    message: str,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    model_risk_detected = any(item.get("detected") for item in categories)
    system_signal_detected = any(item.get("detected") for item in system_signals)
    strongest_code = choose_strongest_code(categories, system_signals)
    response = {
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
    # 모델이 실제로 반환한 usage만 선택적으로 보존한다. local detector, 입력
    # 제한 사전 차단, upstream 실패에는 만들어 낸 토큰 값을 넣지 않는다.
    normalized_usage = normalize_complete_token_usage(usage)
    if normalized_usage is not None:
        response["usage"] = normalized_usage
    return response


def token_usage_from_upstream(response: dict[str, Any]) -> dict[str, int] | None:
    """vLLM chat completion의 완전한 usage만 외부 Risk 계약에 전달한다."""
    return normalize_complete_token_usage(response.get("usage"))


def combine_token_usage(responses: list[dict[str, Any]]) -> dict[str, int] | None:
    """통합 Risk 평가에서 실제 모델 호출들의 usage를 합산한다."""
    usages = [
        usage
        for response in responses
        if (usage := normalize_complete_token_usage(response.get("usage"))) is not None
    ]
    if not usages:
        return None
    return {field: sum(usage[field] for usage in usages) for field in TOKEN_USAGE_FIELDS}


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
