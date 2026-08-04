from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ai_model_serving.domain import ModelRegistry

from .config import RuntimeValidationConfig
from .gpu_checks import sample_gpu as sample_gpu_check
from .http_client import RuntimeValidationHttpClient
from .live_checks import LiveRuntimeChecks
from .reporting import write_reports
from .results import CheckResult
from .soak import SoakRunner

CheckFn = Callable[[], CheckResult]


class RuntimeValidator:
    """runtime validation 검사 실행과 보고서 수집을 조율한다.

    Individual responsibilities live in smaller modules:
    - ``http_client``: auth headers, request encoding, latency measurement
    - ``live_checks``: Gateway/Risk/vLLM/Prometheus/Grafana probes
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

    def run_live(self) -> None:
        self.safe_check("gateway-runtime", "gateway /health", self.live_checks.check_gateway_health)
        self.safe_check("gateway-runtime", "gateway /ready", self.live_checks.check_gateway_ready)
        self.safe_check("gateway-runtime", "gateway /v1/models", self.live_checks.check_models)
        self.safe_check("risk-adapter-runtime", "risk-adapter /health", self.live_checks.check_risk_health)
        self.safe_check("risk-adapter-runtime", "risk-adapter /ready", self.live_checks.check_risk_ready)
        for key, base in self.vllm_bases.items():
            self.safe_check("vllm-runtime", f"{key} /models", lambda key=key, base=base: self.live_checks.check_vllm_models(key, base))
        detectors = self.model_serving.get("risk_adapter", {}).get("detectors", {})
        for key, detector in detectors.items():
            if detector.get("enabled", True) is True:
                route = str(detector.get("route", f"/v1/risk/detectors/{key}/assessments"))
                self.safe_check("risk-adapter-runtime", f"{key} assessment", lambda route=route, key=key: self.live_checks.check_risk_endpoint(route, f"{key} assessment"))
        self.safe_check("risk-adapter-runtime", "aggregate assessment", lambda: self.live_checks.check_risk_endpoint("/v1/risk/assessments", "aggregate assessment"))
        self.safe_check("vllm-runtime", "chat", self.live_checks.check_chat)
        self.safe_check("vllm-runtime", "streaming chat", self.live_checks.check_streaming_chat)
        self.safe_check("vllm-runtime", "embedding", self.live_checks.check_embedding)
        self.safe_check("vllm-runtime", "embedding-ko", self.live_checks.check_embedding_ko)
        self.safe_check("response-format-text-canary", "response_format text", self.live_checks.check_response_format_text)
        self.safe_check("response-format-json-object-canary", "response_format json_object", self.live_checks.check_response_format_json_object)
        self.safe_check("response-format-json-schema-canary", "response_format json_schema", self.live_checks.check_response_format_json_schema)
        self.safe_check("logprobs-non-stream-canary", "logprobs non-stream", self.live_checks.check_logprobs_non_stream)
        self.safe_check("logprobs-stream-canary", "logprobs stream", self.live_checks.check_logprobs_stream)
        self.safe_check("logit-bias-shape-canary", "logit_bias shape", self.live_checks.check_logit_bias_shape)
        self.safe_check("json-schema-with-tools-canary", "json_schema with tools", self.live_checks.check_json_schema_with_tools)
        self.safe_check("json-schema-with-reasoning-canary", "json_schema with reasoning", self.live_checks.check_json_schema_with_reasoning)
        self.safe_check(
            "gemma4-reasoning-parser-structured-outputs-canary",
            "gemma4 reasoning parser structured outputs",
            self.live_checks.check_gemma4_reasoning_parser_structured_outputs,
        )
        self.safe_check("gpu-capacity", "gpu sample before soak", lambda: sample_gpu_check(self.config, self.gpu_budgets, "gpu sample before soak"))
        gateway_metrics = self.monitoring["metric_sources"]["gateway"]["required_metrics"]
        risk_metrics = self.monitoring["metric_sources"]["risk_adapter"]["required_metrics"]
        self.safe_check("monitoring-scrape", "gateway metrics", lambda: self.live_checks.scrape_metrics("gateway", self.gateway_base, gateway_metrics))
        self.safe_check("monitoring-scrape", "risk-adapter metrics", lambda: self.live_checks.scrape_metrics("risk-adapter", self.risk_base, risk_metrics))
        self.safe_check("monitoring-scrape", "prometheus active targets", self.live_checks.check_prometheus_targets)
        self.safe_check("grafana-dashboard-render", "grafana api health", self.live_checks.check_grafana_health)
        self.safe_check("grafana-dashboard-render", "grafana prometheus datasource", self.live_checks.check_grafana_prometheus_datasource)
        self.safe_check("grafana-dashboard-render", "grafana dashboard imports", self.live_checks.check_grafana_dashboard_catalog)
        self.safe_check("gpu-capacity", "soak test", self.soak_runner.run)
        self.safe_check("gpu-capacity", "gpu sample after soak", lambda: sample_gpu_check(self.config, self.gpu_budgets, "gpu sample after soak"))

    def write_reports(self) -> tuple[Path, Path]:
        return write_reports(
            root=self.root,
            output_dir=self.config.output_dir,
            version=self.config.version,
            session_started=self.session_started,
            mode="live",
            results=self.results,
        )
