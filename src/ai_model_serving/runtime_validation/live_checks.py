from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from ai_model_serving.domain import ModelRegistry

from .config import RuntimeValidationConfig
from .constants import FORBIDDEN_RISK_FIELDS
from .http_client import RuntimeValidationHttpClient
from .results import CheckResult


class LiveRuntimeChecks:
    """runtime validation을 위한 live 서비스·모니터링 검사다."""

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
        self._structured_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }

    def _chat_url(self) -> str:
        return f"{self.gateway_base}/v1/chat/completions"

    def _main_model_name(self) -> str:
        return str(self.config.model_serving["models"]["main_llm"]["served_model_name"])

    def _embedding_profile(self, model_id: str) -> tuple[str, int]:
        profile = self.config.model_serving["embedding_profiles"][model_id]
        return str(profile["served_model_name"]), int(profile["default_dimensions"])

    def _structured_response_format(self) -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "runtime_canary",
                "strict": True,
                "schema": dict(self._structured_schema),
            },
        }

    def _choice(self, body: dict[str, Any]) -> dict[str, Any]:
        choices = body.get("choices")
        return choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}

    def _assistant_content(self, body: dict[str, Any]) -> str | None:
        message = self._choice(body).get("message")
        content = message.get("content") if isinstance(message, dict) else None
        return content if isinstance(content, str) else None

    def _content_matches_structured_schema(self, body: dict[str, Any]) -> bool:
        content = self._assistant_content(body)
        if not isinstance(content, str):
            return False
        try:
            parsed = json.loads(content)
            Draft202012Validator(self._structured_schema).validate(parsed)
        except (json.JSONDecodeError, ValidationError):
            return False
        return True

    def _has_valid_tool_calls(self, body: dict[str, Any]) -> bool:
        choice = self._choice(body)
        message = choice.get("message")
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
        if choice.get("finish_reason") != "tool_calls" or not isinstance(tool_calls, list) or not tool_calls:
            return False
        for call in tool_calls:
            if not isinstance(call, dict) or call.get("type") != "function":
                return False
            function = call.get("function")
            if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), str):
                return False
        return True

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
            "model": self._main_model_name(),
            "messages": [{"role": "user", "content": "Say OK only."}],
            "max_tokens": 1,
            "temperature": 0,
        }
        status, body, latency = self.http.json("POST", f"{self.gateway_base}/v1/chat/completions", payload)
        ok = status == 200 and body.get("object") == "chat.completion" and bool(body.get("choices"))
        return CheckResult("vllm-runtime", "gateway chat completion", "pass" if ok else "fail", latency, details={"model": body.get("model"), "choices": len(body.get("choices", []))})

    def check_streaming_chat(self) -> CheckResult:
        payload = {
            "model": self._main_model_name(),
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
        model, dimensions = self._embedding_profile(str(self.config.model_serving["default_embedding_model"]))
        payload = {"model": model, "input": ["runtime validation embedding"]}
        status, body, latency = self.http.json("POST", f"{self.gateway_base}/v1/embeddings", payload)
        data = body.get("data") or []
        vector = data[0].get("embedding") if data and isinstance(data[0], dict) else []
        ok = status == 200 and body.get("object") == "list" and isinstance(vector, list) and len(vector) == dimensions
        return CheckResult("vllm-runtime", "gateway embedding", "pass" if ok else "fail", latency, details={"model": body.get("model"), "dimension": len(vector)})

    def check_embedding_ko(self) -> CheckResult:
        model, dimensions = self._embedding_profile(str(self.config.model_serving["default_retrieval_model"]))
        payload = {"model": model, "input": ["runtime validation Korean retrieval embedding"], "dimensions": dimensions}
        status, body, latency = self.http.json("POST", f"{self.gateway_base}/v1/embeddings", payload)
        data = body.get("data") or []
        vector = data[0].get("embedding") if data and isinstance(data[0], dict) else []
        ok = status == 200 and body.get("object") == "list" and isinstance(vector, list) and len(vector) == dimensions
        return CheckResult("vllm-runtime", "gateway embedding-ko", "pass" if ok else "fail", latency, details={"model": body.get("model"), "dimension": len(vector)})

    def check_response_format_text(self) -> CheckResult:
        payload = {
            "model": self._main_model_name(),
            "messages": [{"role": "user", "content": "Say OK only."}],
            "max_tokens": 8,
            "temperature": 0,
            "response_format": {"type": "text"},
        }
        status, body, latency = self.http.json("POST", self._chat_url(), payload)
        ok = status == 200 and isinstance(self._assistant_content(body), str)
        return CheckResult("response-format-text-canary", "response_format text", "pass" if ok else "fail", latency, details={"status": status, "model": body.get("model")})

    def check_response_format_json_object(self) -> CheckResult:
        payload = {
            "model": self._main_model_name(),
            "messages": [{"role": "user", "content": "Return a JSON object with key answer and string value OK."}],
            "max_tokens": 64,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        status, body, latency = self.http.json("POST", self._chat_url(), payload)
        content = self._assistant_content(body)
        valid_json = False
        if isinstance(content, str):
            try:
                valid_json = isinstance(json.loads(content), (dict, list, str, int, float, bool, type(None)))
            except json.JSONDecodeError:
                valid_json = False
        ok = status == 200 and valid_json
        return CheckResult("response-format-json-object-canary", "response_format json_object", "pass" if ok else "fail", latency, details={"status": status, "valid_json": valid_json})

    def check_response_format_json_schema(self) -> CheckResult:
        payload = {
            "model": self._main_model_name(),
            "messages": [{"role": "user", "content": "Return JSON with exactly one string field named answer."}],
            "max_tokens": 64,
            "temperature": 0,
            "response_format": self._structured_response_format(),
        }
        status, body, latency = self.http.json("POST", self._chat_url(), payload)
        schema_valid = self._content_matches_structured_schema(body)
        ok = status == 200 and schema_valid
        return CheckResult("response-format-json-schema-canary", "response_format json_schema", "pass" if ok else "fail", latency, details={"status": status, "schema_valid": schema_valid, "feature_degraded_on_failure": "structured_outputs"})

    def check_logprobs_non_stream(self) -> CheckResult:
        payload = {
            "model": self._main_model_name(),
            "messages": [{"role": "user", "content": "Say OK only."}],
            "max_tokens": 4,
            "temperature": 0,
            "logprobs": True,
            "top_logprobs": 2,
        }
        status, body, latency = self.http.json("POST", self._chat_url(), payload)
        logprobs = self._choice(body).get("logprobs")
        ok = status == 200 and isinstance(logprobs, dict)
        return CheckResult("logprobs-non-stream-canary", "logprobs non-stream", "pass" if ok else "fail", latency, details={"status": status, "has_choice_logprobs": isinstance(logprobs, dict)})

    def check_logprobs_stream(self) -> CheckResult:
        payload = {
            "model": self._main_model_name(),
            "messages": [{"role": "user", "content": "Say OK only."}],
            "max_tokens": 4,
            "temperature": 0,
            "stream": True,
            "logprobs": True,
        }
        status, content_type, first_chunk_ms, lines, saw_done = self.http.streaming_lines("POST", self._chat_url(), payload)
        saw_logprobs = any('"logprobs"' in line for line in lines)
        ok = status == 200 and content_type.startswith("text/event-stream") and saw_done and saw_logprobs
        return CheckResult(
            "logprobs-stream-canary",
            "logprobs stream",
            "pass" if ok else "fail",
            first_chunk_ms,
            details={"content_type": content_type, "saw_done": saw_done, "saw_logprobs": saw_logprobs, "line_count": len(lines)},
        )

    def check_logit_bias_shape(self) -> CheckResult:
        payload = {
            "model": self._main_model_name(),
            "messages": [{"role": "user", "content": "Say OK only."}],
            "max_tokens": 4,
            "temperature": 0,
            "logit_bias": {"0": 0},
        }
        status, body, latency = self.http.json("POST", self._chat_url(), payload)
        ok = status == 200 and body.get("object") == "chat.completion"
        return CheckResult("logit-bias-shape-canary", "logit_bias shape", "pass" if ok else "fail", latency, details={"status": status, "token_id_semantics": "served_model_tokenizer"})

    def check_json_schema_with_tools(self) -> CheckResult:
        payload = {
            "model": self._main_model_name(),
            "messages": [{"role": "user", "content": "Return JSON with answer, or call get_runtime_answer if needed."}],
            "max_tokens": 96,
            "temperature": 0,
            "response_format": self._structured_response_format(),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_runtime_answer",
                        "description": "Return a short runtime validation answer.",
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"topic": {"type": "string"}},
                            "required": ["topic"],
                        },
                    },
                }
            ],
            "tool_choice": "auto",
        }
        status, body, latency = self.http.json("POST", self._chat_url(), payload)
        schema_valid = self._content_matches_structured_schema(body)
        tool_calls_valid = self._has_valid_tool_calls(body)
        ok = status == 200 and (schema_valid or tool_calls_valid)
        return CheckResult("json-schema-with-tools-canary", "json_schema with tools", "pass" if ok else "fail", latency, details={"status": status, "schema_valid": schema_valid, "tool_calls_valid": tool_calls_valid, "feature_degraded_on_failure": "json_schema_with_tools"})

    def check_json_schema_with_reasoning(self) -> CheckResult:
        payload = {
            "model": self._main_model_name(),
            # Gemma4 thinking은 final answer 이전에 별도 token을 사용한다. 128-token
            # 요청은 정상 reasoning도 중간에 잘라 기능 실패처럼 보인다. 실제 성공에
            # 440 token이 필요했던 고정 문제에 1,024 token 상한을 둔다.
            "messages": [{"role": "user", "content": "What is the derivative of x^3 * ln(x)? Return it in the requested JSON."}],
            "max_tokens": 1024,
            "temperature": 0,
            "reasoning": True,
            "response_format": self._structured_response_format(),
        }
        status, body, latency = self.http.json("POST", self._chat_url(), payload)
        schema_valid = self._content_matches_structured_schema(body)
        choice = self._choice(body)
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        reasoning = message.get("reasoning") if isinstance(message, dict) else None
        ok = status == 200 and choice.get("finish_reason") == "stop" and isinstance(reasoning, str) and bool(reasoning) and schema_valid
        return CheckResult(
            "json-schema-with-reasoning-canary",
            "json_schema with reasoning",
            "pass" if ok else "fail",
            latency,
            details={
                "status": status,
                "finish_reason": choice.get("finish_reason"),
                "has_reasoning": isinstance(reasoning, str) and bool(reasoning),
                "schema_valid": schema_valid,
                "reasoning_normalized_by_gateway": True,
                "feature_degraded_on_failure": "json_schema_with_reasoning",
            },
        )

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
