from __future__ import annotations

import time
from typing import Awaitable, Callable

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response


class Metrics:
    def __init__(self, service: str) -> None:
        self.registry = CollectorRegistry()
        self.service = service
        self.requests = Counter(
            "http_requests_total",
            "Total HTTP requests processed by the service.",
            ["service", "route", "status_code"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency in seconds.",
            ["service", "route"],
            registry=self.registry,
        )
        self.upstream_latency = Histogram(
            "upstream_request_duration_seconds",
            "Upstream runtime request latency in seconds.",
            ["service", "target", "path"],
            registry=self.registry,
        )
        self.upstream_errors = Counter(
            "upstream_errors_total",
            "Upstream runtime errors by target.",
            ["service", "target", "code"],
            registry=self.registry,
        )
        self.auth_failures = Counter(
            "auth_failures_total",
            "Authentication failures by service.",
            ["service", "reason"],
            registry=self.registry,
        )
        self.service_readiness = Gauge(
            "service_readiness_status",
            "Readiness status by dependency, 1 for ready and 0 for not ready.",
            ["service", "dependency"],
            registry=self.registry,
        )
        self.overall_runtime_status = Gauge(
            "overall_runtime_status",
            "Overall service runtime status, 1 for ready and 0 for not ready.",
            ["service"],
            registry=self.registry,
        )
        self.risk_assessments = Counter(
            "risk_assessments_total",
            "Risk assessments by detector and result status.",
            ["service", "detector", "status"],
            registry=self.registry,
        )
        self.risk_assessment_latency = Histogram(
            "risk_assessment_duration_seconds",
            "Risk assessment latency in seconds.",
            ["service", "detector"],
            registry=self.registry,
        )
        self.risk_signal_detected = Counter(
            "risk_signal_detected_total",
            "Detected model risk signals by strongest risk code.",
            ["service", "risk_code"],
            registry=self.registry,
        )
        self.risk_system_signal = Counter(
            "risk_adapter_system_signal_total",
            "Risk adapter system signals by code.",
            ["service", "system_signal_code"],
            registry=self.registry,
        )
        self.forbidden_response_fields = Counter(
            "forbidden_response_field_total",
            "Forbidden response fields observed before response emission.",
            ["service", "field"],
            registry=self.registry,
        )
        self.validation_rejections = Counter(
            "request_validation_rejections_total",
            "Request validation rejections by reason category. Labels never include user text.",
            ["service", "reason"],
            registry=self.registry,
        )
        self.streaming_chunks = Counter(
            "streaming_chunks_total",
            "SSE chunks relayed by the Gateway streaming fast path.",
            ["service", "target"],
            registry=self.registry,
        )
        self.streaming_bytes = Counter(
            "streaming_bytes_total",
            "SSE bytes relayed by the Gateway streaming fast path.",
            ["service", "target"],
            registry=self.registry,
        )
        self.streaming_usage_events = Counter(
            "streaming_usage_events_total",
            "Streaming chunks that included a usage object. The usage payload itself is not exported as metric labels.",
            ["service", "target"],
            registry=self.registry,
        )
        self.streaming_errors = Counter(
            "streaming_errors_total",
            "Streaming fast-path errors by phase. Labels do not include prompt or generated text.",
            ["service", "target", "code", "phase"],
            registry=self.registry,
        )
        self.streaming_requests = Counter(
            "streaming_requests_total",
            "Streaming requests by lifecycle result. Labels never include prompt or generated text.",
            ["service", "target", "status"],
            registry=self.registry,
        )
        self.streaming_time_to_first_chunk = Histogram(
            "streaming_time_to_first_chunk_seconds",
            "Time from accepted streaming request to first relayed SSE chunk.",
            ["service", "target"],
            registry=self.registry,
        )
        self.streaming_duration = Histogram(
            "streaming_duration_seconds",
            "Total streaming relay duration by sanitized terminal status.",
            ["service", "target", "status"],
            registry=self.registry,
        )
        self.streaming_chunks_per_response = Histogram(
            "streaming_chunks_per_response",
            "Number of relayed SSE chunks per streaming response.",
            ["service", "target", "status"],
            registry=self.registry,
        )
        self.streaming_client_disconnects = Counter(
            "streaming_client_disconnects_total",
            "Client disconnects during streaming by phase. Labels do not include user text.",
            ["service", "target", "phase"],
            registry=self.registry,
        )

    async def http_middleware(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        start = time.monotonic()
        status_code = 500
        route = request.url.path
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route_obj = request.scope.get("route")
            if route_obj is not None and getattr(route_obj, "path", None):
                route = route_obj.path
            elapsed = time.monotonic() - start
            self.requests.labels(self.service, route, str(status_code)).inc()
            self.latency.labels(self.service, route).observe(elapsed)
            if status_code == 401:
                self.auth_failures.labels(self.service, "unauthorized").inc()

    def record_upstream_request(self, target: str, path: str, elapsed_seconds: float) -> None:
        self.upstream_latency.labels(self.service, target, path).observe(elapsed_seconds)

    def record_upstream_error(self, target: str, code: str) -> None:
        self.upstream_errors.labels(self.service, target, code).inc()

    def record_validation_rejection(self, reason: str) -> None:
        self.validation_rejections.labels(self.service, reason).inc()

    def record_streaming_chunk(self, target: str, byte_count: int) -> None:
        self.streaming_chunks.labels(self.service, target).inc()
        self.streaming_bytes.labels(self.service, target).inc(max(0, byte_count))

    def record_streaming_usage_event(self, target: str) -> None:
        self.streaming_usage_events.labels(self.service, target).inc()

    def record_streaming_error(self, target: str, code: str, phase: str) -> None:
        self.streaming_errors.labels(self.service, target, code, phase).inc()

    def record_streaming_request_started(self, target: str) -> None:
        self.streaming_requests.labels(self.service, target, "started").inc()

    def record_streaming_first_chunk(self, target: str, elapsed_seconds: float) -> None:
        self.streaming_time_to_first_chunk.labels(self.service, target).observe(elapsed_seconds)

    def record_streaming_completed(self, target: str, status: str, elapsed_seconds: float, chunk_count: int) -> None:
        # Normalize to categorical contract: completed, error, client_disconnect.
        # Uppercase raw codes were used in older callers; map them to the canonical
        # category so dashboard queries on status=~"completed|error|client_disconnect"
        # remain stable across the migration period.
        _CATEGORY_MAP = {
            "completed": "completed",
            "error": "error",
            "client_disconnect": "client_disconnect",
        }
        lower = status.lower()
        sanitized_status = _CATEGORY_MAP.get(lower, "error" if lower not in ("completed", "client_disconnect") else lower)
        self.streaming_requests.labels(self.service, target, sanitized_status).inc()
        self.streaming_duration.labels(self.service, target, sanitized_status).observe(elapsed_seconds)
        self.streaming_chunks_per_response.labels(self.service, target, sanitized_status).observe(max(0, chunk_count))

    def record_streaming_client_disconnect(self, target: str, phase: str) -> None:
        self.streaming_client_disconnects.labels(self.service, target, phase).inc()

    def record_readiness(self, dependency: str, ready: bool) -> None:
        self.service_readiness.labels(self.service, dependency).set(1 if ready else 0)

    def record_overall_readiness(self, ready: bool) -> None:
        self.overall_runtime_status.labels(self.service).set(1 if ready else 0)

    def record_risk_assessment(
        self,
        detector: str,
        status: str,
        elapsed_seconds: float,
        categories: list[dict],
        system_signals: list[dict],
        response: dict,
    ) -> None:
        self.risk_assessments.labels(self.service, detector, status).inc()
        self.risk_assessment_latency.labels(self.service, detector).observe(elapsed_seconds)
        for category in categories:
            if category.get("detected") and category.get("code"):
                self.risk_signal_detected.labels(self.service, category["code"]).inc()
        for signal in system_signals:
            if signal.get("detected") and signal.get("code"):
                self.risk_system_signal.labels(self.service, signal["code"]).inc()
        for field in ["allow", "review", "block", "decision", "action", "safe_to_send", "final_decision"]:
            if field in response:
                self.forbidden_response_fields.labels(self.service, field).inc()

    def response(self) -> Response:
        return Response(generate_latest(self.registry), media_type=CONTENT_TYPE_LATEST)
