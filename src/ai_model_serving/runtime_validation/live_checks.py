from __future__ import annotations

from typing import Any

from ai_model_serving.domain import ModelRegistry

from .config import RuntimeValidationConfig
from .constants import FORBIDDEN_RISK_FIELDS
from .http_client import RuntimeValidationHttpClient
from .results import CheckResult


class LiveRuntimeChecks:
    """Live service and monitoring checks for runtime validation."""

    def __init__(
        self,
        *,
        config: RuntimeValidationConfig,
        registry: ModelRegistry,
        monitoring: dict[str, Any],
        http: RuntimeValidationHttpClient,
    ) -> None:
        self.config = config
        self.registry = registry
        self.monitoring = monitoring
        self.http = http
        self.gateway_base = config.gateway_base
        self.risk_base = config.risk_base
        self.prometheus_base = config.prometheus_base
        self.grafana_base = config.grafana_base
        self.vllm_bases = config.vllm_bases

    def check_gateway_health(self) -> CheckResult:
        status, body, latency = self.http.json("GET", f"{self.gateway_base}/health")
        ok = status == 200 and body.get("status") == "ok" and body.get("service") == "gateway"
        return CheckResult("gateway-runtime", "gateway /health", "pass" if ok else "fail", latency, details=body)

    def check_gateway_ready(self) -> CheckResult:
        status, body, latency = self.http.json("GET", f"{self.gateway_base}/ready", admin=True)
        deps = body.get("dependencies", [])
        ok = status == 200 and body.get("status") == "ready" and all(item.get("status") == "ready" for item in deps)
        return CheckResult("gateway-runtime", "gateway /ready", "pass" if ok else "fail", latency, details=body)

    def check_risk_health(self) -> CheckResult:
        status, body, latency = self.http.json("GET", f"{self.risk_base}/health")
        ok = status == 200 and body.get("status") == "ok" and body.get("service") == "risk-adapter"
        return CheckResult("risk-adapter-runtime", "risk-adapter /health", "pass" if ok else "fail", latency, details=body)

    def check_risk_ready(self) -> CheckResult:
        status, body, latency = self.http.json("GET", f"{self.risk_base}/ready", admin=True)
        deps = body.get("dependencies", [])
        ok = status == 200 and body.get("status") == "ready" and all(item.get("status") == "ready" for item in deps)
        return CheckResult("risk-adapter-runtime", "risk-adapter /ready", "pass" if ok else "fail", latency, details=body)

    def check_models(self) -> CheckResult:
        status, body, latency = self.http.json("GET", f"{self.gateway_base}/v1/models")
        ids = {item.get("id") for item in body.get("data", [])}
        expected = set(self.registry.public_logical_ids())
        ok = status == 200 and expected.issubset(ids)
        return CheckResult("gateway-runtime", "gateway /v1/models", "pass" if ok else "fail", latency, details={"ids": sorted(ids)})

    def check_vllm_models(self, key: str, base_url: str) -> CheckResult:
        status, body, latency = self.http.json("GET", f"{base_url}/models")
        expected_model = self.registry.runtime_service(key).served_model_name
        ids = {item.get("id") for item in body.get("data", [])}
        ok = status == 200 and expected_model in ids
        return CheckResult("vllm-runtime", f"{key} /models", "pass" if ok else "fail", latency, details={"expected_model": expected_model, "ids": sorted(ids)})

    def check_risk_endpoint(self, endpoint: str, check_name: str) -> CheckResult:
        status, body, latency = self.http.json("POST", f"{self.risk_base}{endpoint}", {"prompt": "runtime validation prompt"}, internal=True)
        forbidden = sorted(FORBIDDEN_RISK_FIELDS & set(body))
        ok = status == 200 and body.get("assessment_id") and body.get("status") in {"completed", "partial", "failed"} and not forbidden
        return CheckResult("risk-adapter-runtime", check_name, "pass" if ok else "fail", latency, details={"status": body.get("status"), "forbidden_fields": forbidden})

    def check_chat(self) -> CheckResult:
        payload = {
            "model": "local-main",
            "messages": [{"role": "user", "content": "Say OK only."}],
            "max_tokens": 1,
            "temperature": 0,
        }
        status, body, latency = self.http.json("POST", f"{self.gateway_base}/v1/chat/completions", payload)
        ok = status == 200 and body.get("object") == "chat.completion" and bool(body.get("choices"))
        return CheckResult("vllm-runtime", "gateway chat completion", "pass" if ok else "fail", latency, details={"model": body.get("model"), "choices": len(body.get("choices", []))})

    def check_streaming_chat(self) -> CheckResult:
        payload = {
            "model": "local-main",
            "messages": [{"role": "user", "content": "Say OK only."}],
            "max_tokens": 1,
            "temperature": 0,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        status, content_type, first_chunk_ms, lines, saw_done = self.http.streaming_lines(
            "POST",
            f"{self.gateway_base}/v1/chat/completions",
            payload,
        )
        ok = status == 200 and content_type.startswith("text/event-stream") and first_chunk_ms >= 0 and saw_done
        return CheckResult(
            "vllm-runtime",
            "gateway streaming chat completion",
            "pass" if ok else "fail",
            first_chunk_ms,
            details={
                "content_type": content_type,
                "first_chunk_ms": first_chunk_ms,
                "saw_done": saw_done,
                "line_count": len(lines),
            },
        )

    def check_embedding(self) -> CheckResult:
        payload = {"model": "local-embed", "input": ["runtime validation embedding"], "dimensions": 768}
        status, body, latency = self.http.json("POST", f"{self.gateway_base}/v1/embeddings", payload)
        data = body.get("data") or []
        vector = data[0].get("embedding") if data and isinstance(data[0], dict) else []
        ok = status == 200 and body.get("object") == "list" and isinstance(vector, list) and len(vector) in {128, 256, 512, 768}
        return CheckResult("vllm-runtime", "gateway embedding", "pass" if ok else "fail", latency, details={"model": body.get("model"), "dimension": len(vector)})

    def scrape_metrics(self, service: str, base_url: str, required: list[str], category: str = "monitoring-scrape") -> CheckResult:
        status, text, latency = self.http.text(f"{base_url}/metrics", admin=True)
        present = sorted(metric for metric in required if metric in text)
        missing = sorted(set(required) - set(present))
        ok = status == 200 and not missing
        return CheckResult(category, f"{service} metrics", "pass" if ok else "fail", latency, details={"present": present, "missing": missing})

    def check_prometheus_targets(self) -> CheckResult:
        status, body, latency = self.http.json("GET", f"{self.prometheus_base}/api/v1/targets")
        active = body.get("data", {}).get("activeTargets", []) if isinstance(body, dict) else []
        jobs = {item.get("labels", {}).get("job") for item in active if item.get("health") == "up"}
        expected = {"gateway", "risk-adapter", "vllm-runtimes", "dcgm-exporter", "cadvisor"}
        missing = sorted(expected - jobs)
        ok = status == 200 and not missing
        return CheckResult("monitoring-scrape", "prometheus active targets", "pass" if ok else "fail", latency, details={"up_jobs": sorted(j for j in jobs if j), "missing": missing})

    def check_grafana_health(self) -> CheckResult:
        status, body, latency = self.http.json("GET", f"{self.grafana_base}/api/health", grafana=True)
        ok = status == 200 and str(body.get("database", "")).lower() == "ok"
        return CheckResult("grafana-dashboard-render", "grafana api health", "pass" if ok else "fail", latency, details={"database": body.get("database"), "version": body.get("version")})

    def check_grafana_dashboard_catalog(self) -> CheckResult:
        dashboards_dir = self.config.root / "ops/grafana/dashboards"
        expected = sorted(path.stem for path in dashboards_dir.glob("*.json"))
        found: list[str] = []
        missing: list[str] = []
        max_latency = 0
        for uid in expected:
            status, body, latency = self.http.json("GET", f"{self.grafana_base}/api/dashboards/uid/{uid}", grafana=True)
            max_latency = max(max_latency, latency)
            if status == 200 and body.get("dashboard", {}).get("uid") == uid:
                found.append(uid)
            else:
                missing.append(uid)
        ok = not missing and len(found) == len(expected)
        return CheckResult("grafana-dashboard-render", "grafana dashboard imports", "pass" if ok else "fail", max_latency, details={"found": found, "missing": missing})

    def check_grafana_prometheus_datasource(self) -> CheckResult:
        status, body, latency = self.http.json("GET", f"{self.grafana_base}/api/datasources/name/Prometheus", grafana=True)
        ok = status == 200 and str(body.get("type", "")).lower() == "prometheus"
        return CheckResult("grafana-dashboard-render", "grafana prometheus datasource", "pass" if ok else "fail", latency, details={"type": body.get("type"), "uid": body.get("uid")})
