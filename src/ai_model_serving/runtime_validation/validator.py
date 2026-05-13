from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ai_model_serving.domain import ModelRegistry

from .config import RuntimeValidationConfig
from .config_checks import ConfigOnlyChecks
from .gpu_checks import sample_gpu as sample_gpu_check
from .http_client import RuntimeValidationHttpClient
from .live_checks import LiveRuntimeChecks
from .reporting import write_reports
from .results import CheckResult
from .soak import SoakRunner

CheckFn = Callable[[], CheckResult]


class RuntimeValidator:
    """Orchestrate runtime validation checks and report collection.

    Individual responsibilities live in smaller modules:
    - ``http_client``: auth headers, request encoding, latency measurement
    - ``live_checks``: Gateway/Risk/vLLM/Prometheus/Grafana probes
    - ``config_checks``: registry-backed projection and governance checks
    - ``gpu_checks`` / ``soak``: host GPU sampling and concurrent smoke loop
    """

    def __init__(self, config: RuntimeValidationConfig) -> None:
        self.config = config
        self.root = config.root
        self.model_serving = config.model_serving
        self.registry = ModelRegistry(config.model_catalog, config.model_serving)
        self.monitoring = config.monitoring
        self.gpu_budgets = config.gpu_budgets
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
        self.soak_runner = SoakRunner(config=config, http=self.http)

    def headers(self, *, internal: bool = False, admin: bool = False) -> dict[str, str]:
        return self.http.headers(internal=internal, admin=admin)

    def http_json(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.http.json(*args, **kwargs)

    def http_text(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.http.text(*args, **kwargs)

    def record(self, result: CheckResult) -> None:
        self.results.append(result)
        marker = "PASS" if result.passed else "FAIL"
        print(f"[{marker}] {result.category}::{result.name} {result.detail}")

    def safe_check(self, category: str, name: str, fn: CheckFn) -> None:
        try:
            result = fn()
            self.record(result)
        except Exception as exc:  # noqa: BLE001 - runtime validation should capture all failures.
            self.record(CheckResult(category, name, "fail", detail=f"{type(exc).__name__}: {exc}"))

    # Backward-compatible wrappers for scripts/tests that call individual checks.
    def check_gateway_health(self) -> CheckResult:
        return self.live_checks.check_gateway_health()

    def check_gateway_ready(self) -> CheckResult:
        return self.live_checks.check_gateway_ready()

    def check_risk_health(self) -> CheckResult:
        return self.live_checks.check_risk_health()

    def check_risk_ready(self) -> CheckResult:
        return self.live_checks.check_risk_ready()

    def check_models(self) -> CheckResult:
        return self.live_checks.check_models()

    def check_vllm_models(self, key: str, base_url: str) -> CheckResult:
        return self.live_checks.check_vllm_models(key, base_url)

    def check_risk_endpoint(self, endpoint: str, check_name: str) -> CheckResult:
        return self.live_checks.check_risk_endpoint(endpoint, check_name)

    def check_chat(self) -> CheckResult:
        return self.live_checks.check_chat()

    def check_streaming_chat(self) -> CheckResult:
        return self.live_checks.check_streaming_chat()

    def check_embedding(self) -> CheckResult:
        return self.live_checks.check_embedding()

    def check_response_format_text(self) -> CheckResult:
        return self.live_checks.check_response_format_text()

    def check_response_format_json_object(self) -> CheckResult:
        return self.live_checks.check_response_format_json_object()

    def check_response_format_json_schema(self) -> CheckResult:
        return self.live_checks.check_response_format_json_schema()

    def check_logprobs_non_stream(self) -> CheckResult:
        return self.live_checks.check_logprobs_non_stream()

    def check_logprobs_stream(self) -> CheckResult:
        return self.live_checks.check_logprobs_stream()

    def check_logit_bias_shape(self) -> CheckResult:
        return self.live_checks.check_logit_bias_shape()

    def check_json_schema_with_tools(self) -> CheckResult:
        return self.live_checks.check_json_schema_with_tools()

    def check_json_schema_with_reasoning(self) -> CheckResult:
        return self.live_checks.check_json_schema_with_reasoning()

    def check_gemma4_reasoning_parser_structured_outputs(self) -> CheckResult:
        return self.live_checks.check_gemma4_reasoning_parser_structured_outputs()

    def scrape_metrics(self, service: str, base_url: str, required: list[str], category: str = "monitoring-scrape") -> CheckResult:
        return self.live_checks.scrape_metrics(service, base_url, required, category)

    def check_prometheus_targets(self) -> CheckResult:
        return self.live_checks.check_prometheus_targets()

    def check_grafana_health(self) -> CheckResult:
        return self.live_checks.check_grafana_health()

    def check_grafana_dashboard_catalog(self) -> CheckResult:
        return self.live_checks.check_grafana_dashboard_catalog()

    def check_grafana_prometheus_datasource(self) -> CheckResult:
        return self.live_checks.check_grafana_prometheus_datasource()

    def sample_gpu(self, name: str) -> CheckResult:
        return sample_gpu_check(self.config, self.gpu_budgets, name)

    def run_soak_once(self, index: int) -> dict[str, object]:
        return self.soak_runner.run_once(index)

    def run_soak(self) -> CheckResult:
        return self.soak_runner.run()

    def run_config_only(self) -> None:
        ConfigOnlyChecks(config=self.config, registry=self.registry, record=self.record).run()

    def run_live(self) -> None:
        self.safe_check("gateway-runtime", "gateway /health", self.check_gateway_health)
        self.safe_check("gateway-runtime", "gateway /ready", self.check_gateway_ready)
        self.safe_check("gateway-runtime", "gateway /v1/models", self.check_models)
        self.safe_check("risk-adapter-runtime", "risk-adapter /health", self.check_risk_health)
        self.safe_check("risk-adapter-runtime", "risk-adapter /ready", self.check_risk_ready)
        for key, base in self.vllm_bases.items():
            self.safe_check("vllm-runtime", f"{key} /models", lambda key=key, base=base: self.check_vllm_models(key, base))
        detectors = self.model_serving.get("risk_adapter", {}).get("detectors", {})
        for key, detector in detectors.items():
            if detector.get("enabled", True) is True:
                route = str(detector.get("route", f"/v1/risk/detectors/{key}/assessments"))
                self.safe_check("risk-adapter-runtime", f"{key} assessment", lambda route=route, key=key: self.check_risk_endpoint(route, f"{key} assessment"))
        self.safe_check("risk-adapter-runtime", "aggregate assessment", lambda: self.check_risk_endpoint("/v1/risk/assessments", "aggregate assessment"))
        self.safe_check("vllm-runtime", "chat", self.check_chat)
        self.safe_check("vllm-runtime", "streaming chat", self.check_streaming_chat)
        self.safe_check("vllm-runtime", "embedding", self.check_embedding)
        self.safe_check("response-format-text-canary", "response_format text", self.check_response_format_text)
        self.safe_check("response-format-json-object-canary", "response_format json_object", self.check_response_format_json_object)
        self.safe_check("response-format-json-schema-canary", "response_format json_schema", self.check_response_format_json_schema)
        self.safe_check("logprobs-non-stream-canary", "logprobs non-stream", self.check_logprobs_non_stream)
        self.safe_check("logprobs-stream-canary", "logprobs stream", self.check_logprobs_stream)
        self.safe_check("logit-bias-shape-canary", "logit_bias shape", self.check_logit_bias_shape)
        self.safe_check("json-schema-with-tools-canary", "json_schema with tools", self.check_json_schema_with_tools)
        self.safe_check("json-schema-with-reasoning-canary", "json_schema with reasoning", self.check_json_schema_with_reasoning)
        self.safe_check(
            "gemma4-reasoning-parser-structured-outputs-canary",
            "gemma4 reasoning parser structured outputs",
            self.check_gemma4_reasoning_parser_structured_outputs,
        )
        self.safe_check("gpu-capacity", "gpu sample before soak", lambda: self.sample_gpu("gpu sample before soak"))
        gateway_metrics = self.monitoring["metric_sources"]["gateway"]["required_metrics"]
        risk_metrics = self.monitoring["metric_sources"]["risk_adapter"]["required_metrics"]
        self.safe_check("monitoring-scrape", "gateway metrics", lambda: self.scrape_metrics("gateway", self.gateway_base, gateway_metrics))
        self.safe_check("monitoring-scrape", "risk-adapter metrics", lambda: self.scrape_metrics("risk-adapter", self.risk_base, risk_metrics))
        self.safe_check("monitoring-scrape", "prometheus active targets", self.check_prometheus_targets)
        self.safe_check("grafana-dashboard-render", "grafana api health", self.check_grafana_health)
        self.safe_check("grafana-dashboard-render", "grafana prometheus datasource", self.check_grafana_prometheus_datasource)
        self.safe_check("grafana-dashboard-render", "grafana dashboard imports", self.check_grafana_dashboard_catalog)
        self.safe_check("gpu-capacity", "soak test", self.run_soak)
        self.safe_check("gpu-capacity", "gpu sample after soak", lambda: self.sample_gpu("gpu sample after soak"))

    def write_reports(self) -> tuple[Path, Path]:
        return write_reports(
            root=self.root,
            output_dir=self.config.output_dir,
            version=self.config.version,
            session_started=self.session_started,
            mode="config-only" if self.config.config_only else "live",
            results=self.results,
        )
