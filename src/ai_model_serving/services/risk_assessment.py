from __future__ import annotations

import time
from typing import Any, Protocol

from ..errors import ServiceError
from ..metrics import Metrics
from ..risk_input import RiskInputPolicy
from ..risk import (
    DetectorSpec,
    assessment_response,
    extract_generation_text,
    parse_detector_label,
    system_signal,
)


class RiskClientSet(Protocol):
    detectors: dict[str, Any]


class RiskAssessmentService:
    """Use-case layer for detector assessment and aggregation.

    The service keeps detector orchestration independent from the FastAPI
    transport layer.  That makes it easier to add future detectors, alternate
    runtimes, or offline harness execution without copying endpoint code.
    """

    def __init__(
        self,
        clients: RiskClientSet,
        metrics: Metrics | None = None,
        input_policy: RiskInputPolicy | None = None,
        detector_specs: dict[str, DetectorSpec] | None = None,
        aggregate_detector_order: tuple[str, ...] = (),
    ) -> None:
        self.clients = clients
        self.metrics = metrics
        self.input_policy = input_policy
        self.detector_specs = detector_specs or {}
        self.aggregate_detector_order = aggregate_detector_order or tuple(self.detector_specs)

    async def assess_prompt(self, prompt: str) -> dict[str, Any]:
        return await self.assess_detector_key("prompt", prompt)

    async def assess_siren(self, prompt: str) -> dict[str, Any]:
        return await self.assess_detector_key("siren", prompt)

    async def assess_aggregate(self, prompt: str) -> dict[str, Any]:
        start = time.monotonic()
        results = [await self.assess_detector_key(detector_key, prompt) for detector_key in self.aggregate_detector_order]
        categories = [category for result in results for category in result["categories"]]
        system_signals = [signal for result in results for signal in result["system_signals"]]
        status = "completed" if results and all(result["status"] == "completed" for result in results) else "partial"
        model_risk = any(item.get("detected") for item in categories)
        system_risk = any(item.get("detected") for item in system_signals)
        if model_risk:
            message = "Risk signal detected."
        elif system_risk:
            message = "Risk assessment completed with system signals."
        else:
            message = "No model risk signal detected."
        response = assessment_response(categories=categories, system_signals=system_signals, status=status, message=message)
        if self.metrics is not None:
            self.metrics.record_risk_assessment(
                "aggregate",
                response["status"],
                time.monotonic() - start,
                response["categories"],
                response["system_signals"],
                response,
            )
        return response

    async def assess_detector_key(self, detector_key: str, prompt: str) -> dict[str, Any]:
        if detector_key not in self.detector_specs:
            raise ServiceError("DETECTOR_DISABLED", f"Risk detector is not enabled: {detector_key}", False, 410)
        try:
            client = self.clients.detectors[detector_key]
        except KeyError as exc:
            raise ServiceError("DETECTOR_DISABLED", f"Risk detector client is not configured: {detector_key}", False, 410) from exc
        return await self.assess_detector(client, self.detector_specs[detector_key], prompt)

    async def assess_detector(self, client: Any, detector: DetectorSpec, prompt: str) -> dict[str, Any]:
        start = time.monotonic()
        try:
            if self.input_policy is not None and self.input_policy.overflowed(prompt):
                response = self.input_policy.system_signal_response(
                    detector_name=detector.name,
                    source_model=detector.source_model,
                )
                return self._record_detector_result(detector.name, start, response)
            category = await self._call_detector(client, detector, prompt)
            message = "Risk signal detected." if category["detected"] else "No model risk signal detected."
            response = assessment_response(categories=[category], system_signals=[], status="completed", message=message)
        except ServiceError as exc:
            code = system_signal_code(exc)
            response = assessment_response(
                categories=[],
                system_signals=[system_signal(code, exc.message, detector.source_model, exc.retryable)],
                status="failed",
                message="Detector inference failed." if code != "PARSE_ERROR" else "Detector output could not be parsed.",
            )
        return self._record_detector_result(detector.name, start, response)

    def _record_detector_result(self, detector_name: str, start: float, response: dict[str, Any]) -> dict[str, Any]:
        if self.metrics is not None:
            self.metrics.record_risk_assessment(
                detector_name,
                response["status"],
                time.monotonic() - start,
                response["categories"],
                response["system_signals"],
                response,
            )
        return response

    async def _call_detector(self, client: Any, detector: DetectorSpec, prompt: str) -> dict[str, Any]:
        payload = {
            "model": client.endpoint.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": detector.max_output_tokens,
            "temperature": detector.temperature,
        }
        start = time.monotonic()
        try:
            response = await client.post_json("chat/completions", payload)
        except ServiceError as exc:
            if self.metrics is not None:
                self.metrics.record_upstream_error(detector.source_model, exc.code)
            raise
        finally:
            if self.metrics is not None:
                self.metrics.record_upstream_request(detector.source_model, "chat/completions", time.monotonic() - start)
        text = extract_generation_text(response, expected_model=client.endpoint.model)
        return parse_detector_label(text, detector)


def system_signal_code(exc: ServiceError) -> str:
    if exc.code == "UPSTREAM_TIMEOUT":
        return "INFERENCE_TIMEOUT"
    if exc.code == "QUEUE_TIMEOUT":
        return "INFERENCE_QUEUE_TIMEOUT"
    if exc.code in {"PARSE_ERROR", "UPSTREAM_SCHEMA_ERROR"}:
        return "PARSE_ERROR"
    return "INFERENCE_ERROR"
