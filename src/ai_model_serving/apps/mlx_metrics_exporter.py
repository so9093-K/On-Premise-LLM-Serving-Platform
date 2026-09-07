"""Translate MLX-VLM's JSON runtime metrics into Prometheus exposition."""
from __future__ import annotations

import functools
import json
import math
import os
import urllib.error
import urllib.request
from typing import Any

from fastapi import FastAPI
from starlette.responses import PlainTextResponse

from ai_model_serving.configuration import load_yaml_mapping
from ai_model_serving.project_paths import resolve_project_root


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _sample(name: str, kind: str, help_text: str, value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    return f"# HELP {name} {help_text}\n# TYPE {name} {kind}\n{name} {number:g}\n"


def render_mlx_metrics(payload: dict[str, Any] | None, *, scrape_success: bool) -> str:
    """Render only stable numeric fields; recent request bodies/errors are excluded."""
    output = _sample(
        "mlx_runtime_scrape_success",
        "gauge",
        "Whether the latest MLX-VLM JSON metrics fetch succeeded.",
        1 if scrape_success else 0,
    )
    if not isinstance(payload, dict):
        return output
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    server = payload.get("server") if isinstance(payload.get("server"), dict) else {}
    latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else {}

    counters = {
        "requests_started": ("requests_started_total", "Requests accepted since MLX-VLM startup."),
        "requests_completed": ("requests_completed_total", "Requests completed since MLX-VLM startup."),
        "requests_failed": ("requests_failed_total", "Requests failed since MLX-VLM startup."),
        "streaming_requests": ("streaming_requests_total", "Streaming requests accepted since MLX-VLM startup."),
        "prompt_tokens_total": ("prompt_tokens_total", "Prompt tokens processed since MLX-VLM startup."),
        "completion_tokens_total": ("completion_tokens_total", "Completion tokens emitted since MLX-VLM startup."),
        "generated_tokens_total": ("generated_tokens_total", "Generated tokens processed since MLX-VLM startup."),
    }
    for source, (metric, help_text) in counters.items():
        output += _sample(f"mlx_runtime_{metric}", "counter", help_text, summary.get(source))

    gauges = {
        "uptime_s": "MLX-VLM process uptime in seconds.",
        "in_flight": "Requests currently executing in MLX-VLM.",
        "avg_request_time_s": "Average completed request duration in seconds.",
        "avg_request_tok_s": "Average completion throughput over total request time.",
        "avg_decode_tok_s": "Average decode throughput over decode time.",
    }
    for source, help_text in gauges.items():
        output += _sample(f"mlx_runtime_{source}", "gauge", help_text, summary.get(source))

    output += _sample(
        "mlx_runtime_loaded", "gauge", "Whether the configured text model is loaded.",
        1 if server.get("loaded_model") else 0,
    )
    output += _sample(
        "mlx_runtime_request_queue_depth", "gauge", "Requests waiting in the MLX-VLM queue.",
        server.get("request_queue_depth"),
    )
    output += _sample(
        "mlx_runtime_effective_context_limit_tokens", "gauge",
        "Effective MLX-VLM context limit in tokens.", server.get("effective_context_limit"),
    )
    output += _sample(
        "mlx_runtime_latest_prefill_tokens_per_second", "gauge",
        "Prefill throughput for the latest completed request.", latest.get("prefill_tok_s"),
    )
    output += _sample(
        "mlx_runtime_latest_decode_tokens_per_second", "gauge",
        "Decode throughput for the latest completed request.", latest.get("decode_tok_s"),
    )
    output += _sample(
        "mlx_runtime_latest_peak_memory_gib", "gauge",
        "MLX peak memory reported for the latest completed request in GiB.", latest.get("peak_memory_gb"),
    )
    return output


def _fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("MLX metrics response must be a JSON object")
    return payload


@functools.cache
def upstream_metrics_url() -> str:
    """Resolve the native MLX-VLM metrics URL.

    Port와 경로는 configs/macos_mlx_runtime.yaml이 소유한다. 컨테이너에서 host를
    어떻게 부르는지는 배치 문제라 compose가 MLX_RUNTIME_METRICS_HOST로 넘긴다.
    전체 URL을 직접 지정해야 하는 운영 상황에서는 MLX_RUNTIME_METRICS_URL이 이긴다.
    """
    override = os.environ.get("MLX_RUNTIME_METRICS_URL", "").strip()
    if override:
        return override
    runtime = load_yaml_mapping(
        resolve_project_root() / "configs" / "macos_mlx_runtime.yaml"
    )["runtime"]
    host = os.environ.get("MLX_RUNTIME_METRICS_HOST", "").strip() or str(runtime["host"])
    return f"http://{host}:{runtime['port']}{runtime['metrics_path']}"


app = FastAPI(title="MLX Metrics Exporter", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    try:
        payload = _fetch_json(upstream_metrics_url())
        body = render_mlx_metrics(payload, scrape_success=True)
    except (OSError, RuntimeError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        body = render_mlx_metrics(None, scrape_success=False)
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")
