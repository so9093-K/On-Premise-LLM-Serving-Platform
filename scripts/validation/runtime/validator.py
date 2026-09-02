from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ai_model_serving.domain import ModelRegistry

from .config import RuntimeValidationConfig
from .http_client import RuntimeValidationHttpClient
from .live_checks import LiveRuntimeChecks
from .reporting import write_reports
from .results import CheckResult

CheckFn = Callable[[], CheckResult]


class RuntimeValidator:
    """runtime validation 검사 실행과 보고서 수집을 조율한다.

    Individual responsibilities live in smaller modules:
    - ``http_client``: auth headers, request encoding, latency measurement
    - ``live_checks``: Gateway/Risk/vLLM/Prometheus/Grafana probes
    """

    def __init__(self, config: RuntimeValidationConfig) -> None:
        self.config = config
        self.root = config.root
        self.model_serving = config.model_serving
        self.registry = ModelRegistry(config.model_catalog, config.model_serving)
        self.monitoring = config.monitoring
        self.results: list[CheckResult] = []
        self.session_started = datetime.now(timezone.utc).isoformat()
        self.gateway_base = config.gateway_base
        self.risk_base = config.risk_base
        self.prometheus_base = config.prometheus_base
        self.grafana_base = config.grafana_base
        self.vllm_bases = config.vllm_bases
        self.http = RuntimeValidationHttpClient(config)
        self.live_checks = LiveRuntimeChecks(
            config=config,
            registry=self.registry,
            monitoring=self.monitoring,
            http=self.http,
        )

    def headers(self, *, internal: bool = False, admin: bool = False) -> dict[str, str]:
        return self.http.headers(internal=internal, admin=admin)

    def record(self, result: CheckResult) -> None:
        self.results.append(result)
        marker = "PASS" if result.passed else "SKIP" if result.status == "skip" else "FAIL"
        print(f"[{marker}] {result.category}::{result.name} {result.detail}")

    def safe_check(self, category: str, name: str, fn: CheckFn) -> CheckResult:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - runtime validation should capture all failures.
            result = CheckResult(category, name, "fail", detail=f"{type(exc).__name__}: {exc}")
        self.record(result)
        return result

    def skip_check(self, category: str, name: str, *, missing_parameters: set[str]) -> None:
        self.record(
            CheckResult(
                category,
                name,
                "skip",
                detail="active main-model profile does not expose required request parameter(s)",
                details={"missing_parameters": sorted(missing_parameters)},
            )
        )

    def run_live(self) -> None:
        self.safe_check("gateway-runtime", "gateway /health", self.live_checks.check_gateway_health)
        self.safe_check("gateway-runtime", "gateway /ready", self.live_checks.check_gateway_ready)
        model_listing = self.safe_check("gateway-runtime", "gateway /v1/models", self.live_checks.check_models)
        self.safe_check("risk-adapter-runtime", "risk-adapter /health", self.live_checks.check_risk_health)
        self.safe_check("risk-adapter-runtime", "risk-adapter /ready", self.live_checks.check_risk_ready)
        for key, base in self.vllm_bases.items():
            self.safe_check("vllm-runtime", f"{key} /models", lambda key=key, base=base: self.live_checks.check_vllm_models(key, base))
        detectors = self.model_serving.get("risk_adapter", {}).get("detectors", {})
        for key, detector in detectors.items():
            if detector.get("enabled", True) is True:
                route = str(detector.get("route", f"/v1/risk/detectors/{key}/assessments"))
                self.safe_check("risk-adapter-runtime", f"{key} assessment", lambda route=route, key=key: self.live_checks.check_risk_endpoint(route, f"{key} assessment", key))
        self.safe_check("risk-adapter-runtime", "aggregate assessment", lambda: self.live_checks.check_risk_endpoint("/v1/risk/assessments", "aggregate assessment"))
        self.safe_check("risk-adapter-runtime", "detector latency at contract limit", self.live_checks.check_risk_latency_under_load)
        self.safe_check("vllm-runtime", "chat", self.live_checks.check_chat)
        self.safe_check("vllm-runtime", "streaming chat", self.live_checks.check_streaming_chat)
        self.safe_check("vllm-runtime", "embedding", self.live_checks.check_embedding)
        self.safe_check("vllm-runtime", "embedding-ko", self.live_checks.check_embedding_ko)
        parameters = model_listing.details.get("main_model_request_parameters", {})
        supported = set(parameters) if isinstance(parameters, dict) else set()
        response_format = parameters.get("response_format", {}) if isinstance(parameters, dict) else {}
        response_types = set(response_format.get("allowed_types", [])) if isinstance(response_format, dict) else set()

        def run_when_supported(
            category: str,
            name: str,
            required: set[str],
            fn: CheckFn,
            *,
            response_type: str | None = None,
        ) -> None:
            missing = required - supported
            if response_type is not None and response_type not in response_types:
                missing.add(f"response_format.type={response_type}")
            if missing:
                self.skip_check(category, name, missing_parameters=missing)
            else:
                self.safe_check(category, name, fn)

        for category, name, required, fn, response_type in (
            ("response-format-text-canary", "response_format text", {"response_format"}, self.live_checks.check_response_format_text, "text"),
            ("response-format-json-object-canary", "response_format json_object", {"response_format"}, self.live_checks.check_response_format_json_object, "json_object"),
            ("response-format-json-schema-canary", "response_format json_schema", {"response_format"}, self.live_checks.check_response_format_json_schema, "json_schema"),
            ("logprobs-non-stream-canary", "logprobs non-stream", {"logprobs"}, self.live_checks.check_logprobs_non_stream, None),
            ("logprobs-stream-canary", "logprobs stream", {"logprobs"}, self.live_checks.check_logprobs_stream, None),
            ("logit-bias-shape-canary", "logit_bias shape", {"logit_bias"}, self.live_checks.check_logit_bias_shape, None),
            ("json-schema-with-tools-canary", "json_schema with tools", {"response_format", "tools"}, self.live_checks.check_json_schema_with_tools, "json_schema"),
            ("json-schema-with-reasoning-canary", "json_schema with reasoning", {"response_format", "reasoning"}, self.live_checks.check_json_schema_with_reasoning, "json_schema"),
        ):
            run_when_supported(category, name, required, fn, response_type=response_type)
        gateway_metrics = self.monitoring["metric_sources"]["gateway"]["required_metrics"]
        risk_metrics = self.monitoring["metric_sources"]["risk_adapter"]["required_metrics"]
        self.safe_check("monitoring-scrape", "gateway metrics", lambda: self.live_checks.scrape_metrics("gateway", self.gateway_base, gateway_metrics))
        self.safe_check("monitoring-scrape", "risk-adapter metrics", lambda: self.live_checks.scrape_metrics("risk-adapter", self.risk_base, risk_metrics))
        self.safe_check("monitoring-scrape", "prometheus active targets", self.live_checks.check_prometheus_targets)
        self.safe_check("grafana-dashboard-render", "grafana api health", self.live_checks.check_grafana_health)
        self.safe_check("grafana-dashboard-render", "grafana prometheus datasource", self.live_checks.check_grafana_prometheus_datasource)
        self.safe_check("grafana-dashboard-render", "grafana dashboard imports", self.live_checks.check_grafana_dashboard_catalog)

    def write_reports(self) -> tuple[Path, Path]:
        return write_reports(
            root=self.root,
            output_dir=self.config.output_dir,
            version=self.config.version,
            session_started=self.session_started,
            mode="live",
            results=self.results,
        )
