from __future__ import annotations

from typing import Any

from .env import as_float, as_int, env
from .types import RuntimeEndpoint


def build_runtime_endpoint(
    *,
    model_key: str,
    env_url: str,
    env_model: str,
    timeout: float,
    models: dict[str, Any],
    operational_limits: dict[str, Any],
) -> RuntimeEndpoint:
    cfg = models[model_key]
    env_prefix = model_key.upper()
    http_limits = operational_limits.get("http_client", {})
    resource_control = cfg.get("resource_control", {})
    admission_control = resource_control.get("admission_control", {})
    request_limits = resource_control.get("request_limits", {})
    default_model_concurrency = int(operational_limits.get("per_upstream_concurrency", 1))
    default_queue_timeout = float(operational_limits.get("queue_timeout_seconds", 2))
    default_failure_threshold = int(operational_limits.get("circuit_breaker_failure_threshold", 3))
    default_reset_seconds = float(operational_limits.get("circuit_breaker_reset_seconds", 15))
    default_max_connections = int(http_limits.get("max_connections", 100))
    default_keepalive = int(http_limits.get("max_keepalive_connections", 20))
    endpoint_timeout = as_float(
        f"{env_prefix}_TIMEOUT_SECONDS",
        float(cfg.get("timeout_seconds", timeout)),
        minimum=0.1,
    )
    return RuntimeEndpoint(
        logical_id=str(cfg["served_model_name"]),
        base_url=env(env_url, str(cfg["endpoint"])).rstrip("/"),
        model=env(env_model, str(cfg["served_model_name"])),
        timeout_seconds=endpoint_timeout,
        max_concurrency=as_int(
            f"{env_prefix}_MAX_CONCURRENCY",
            int(cfg.get("gateway_max_concurrency", admission_control.get("max_concurrency", default_model_concurrency))),
        ),
        queue_timeout_seconds=as_float(
            f"{env_prefix}_QUEUE_TIMEOUT_SECONDS",
            float(cfg.get("queue_timeout_seconds", admission_control.get("queue_timeout_seconds", default_queue_timeout))),
        ),
        circuit_breaker_failure_threshold=as_int(
            f"{env_prefix}_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
            int(cfg.get("circuit_breaker_failure_threshold", default_failure_threshold)),
        ),
        circuit_breaker_reset_seconds=as_float(
            f"{env_prefix}_CIRCUIT_BREAKER_RESET_SECONDS",
            float(cfg.get("circuit_breaker_reset_seconds", default_reset_seconds)),
            minimum=0.1,
        ),
        http_max_connections=as_int("HTTP_MAX_CONNECTIONS", default_max_connections),
        http_max_keepalive_connections=as_int("HTTP_MAX_KEEPALIVE_CONNECTIONS", default_keepalive),
        max_output_tokens=(int(cfg["max_output_tokens"]) if "max_output_tokens" in cfg else None),
        max_model_len=(int(cfg["max_model_len"]) if "max_model_len" in cfg else None),
        allowed_input_modalities=tuple(str(item) for item in request_limits.get("input_modalities", [request_limits.get("input_modality", "text")])),
        max_image_inputs=int(request_limits.get("max_image_inputs", 0)),
        allowed_image_url_schemes=tuple(str(item) for item in request_limits.get("allowed_image_url_schemes", [])),
        max_image_bytes=int(request_limits.get("max_image_bytes", 0)),
        max_image_pixels=int(request_limits.get("max_image_pixels", 0)),
        allowed_image_mime_types=tuple(str(item) for item in request_limits.get("allowed_image_mime_types", [])),
        request_parameter_policy=cfg.get("request_parameter_policy", {}),
        runtime_features=cfg.get("runtime_features", {}),
    )


def validate_timeout_budget(
    *,
    gateway_timeout_seconds: float,
    risk_adapter_timeout_seconds: float,
    main_llm: RuntimeEndpoint,
    risk_prompt: RuntimeEndpoint,
    risk_siren: RuntimeEndpoint,
    risk_adapter_execution: str,
) -> None:
    if gateway_timeout_seconds < main_llm.timeout_seconds:
        raise RuntimeError("REQUEST_TIMEOUT_SECONDS must be greater than or equal to MAIN_LLM_TIMEOUT_SECONDS.")
    if gateway_timeout_seconds < risk_adapter_timeout_seconds:
        raise RuntimeError("REQUEST_TIMEOUT_SECONDS must be greater than or equal to RISK_ADAPTER_TIMEOUT_SECONDS.")
    if risk_adapter_execution == "sequential":
        aggregate_budget = (
            risk_prompt.queue_timeout_seconds
            + risk_prompt.timeout_seconds
            + risk_siren.queue_timeout_seconds
            + risk_siren.timeout_seconds
        )
        if risk_adapter_timeout_seconds < aggregate_budget:
            raise RuntimeError("RISK_ADAPTER_TIMEOUT_SECONDS must cover sequential risk detector queue and inference budgets.")
