from __future__ import annotations

from typing import Any

from ai_model_serving.domain import ModelRegistry


def prometheus_scrape_config_document(
    *, registry: ModelRegistry, monitoring: dict[str, Any], services: dict[str, Any]
) -> dict[str, Any]:
    """Build the Prometheus configuration generated from the runtime registry."""
    stack = monitoring.get("monitoring_stack", {})
    metric_sources = monitoring.get("metric_sources", {})
    gateway = metric_sources.get("gateway", {})
    risk = metric_sources.get("risk_adapter", {})
    vllm = metric_sources.get("vllm_instances", {})
    dcgm = stack.get("dcgm_exporter", {})
    cadvisor = stack.get("cadvisor", {})
    gateway_service = str(services["gateway"]["compose_service"])
    risk_service = str(services["risk_adapter"]["compose_service"])
    dcgm_service = str(services["dcgm_exporter"]["compose_service"])
    cadvisor_service = str(services["cadvisor"]["compose_service"])
    static_vllm_configs = [
        {
            "targets": [f"{target.compose_service_name}:{target.port}"],
            "labels": {
                "model": target.logical_id,
                "runtime_service": target.compose_service_name,
            },
        }
        for target in registry.runtime_validation_targets()
    ]
    return {
        "global": {
            "scrape_interval": stack.get("prometheus", {}).get("scrape_interval", "15s"),
            "scrape_timeout": stack.get("prometheus", {}).get("scrape_timeout", "10s"),
            "evaluation_interval": stack.get("prometheus", {}).get("scrape_interval", "15s"),
        },
        "rule_files": list(stack.get("prometheus", {}).get("rule_files", [])),
        "scrape_configs": [
            {
                "job_name": "gateway",
                "metrics_path": gateway.get("metrics_path", "/metrics"),
                "bearer_token_file": "/run/secrets/admin_api_key",
                "static_configs": [{"targets": [f"{gateway_service}:{services['gateway']['container_port']}"]}],
            },
            {
                "job_name": "risk-adapter",
                "metrics_path": risk.get("metrics_path", "/metrics"),
                "bearer_token_file": "/run/secrets/admin_api_key",
                "static_configs": [{"targets": [f"{risk_service}:{services['risk_adapter']['container_port']}"]}],
            },
            {
                "job_name": vllm.get("scrape_job", "vllm-runtimes"),
                "metrics_path": vllm.get("metrics_path", "/metrics"),
                "static_configs": static_vllm_configs,
            },
            {
                "job_name": "dcgm-exporter",
                "metrics_path": dcgm.get("default_metrics_path", "/metrics"),
                "static_configs": [{"targets": [f"{dcgm_service}:{dcgm.get('internal_port', 9400)}"]}],
            },
            {
                "job_name": "cadvisor",
                "metrics_path": cadvisor.get("default_metrics_path", "/metrics"),
                "static_configs": [{"targets": [f"{cadvisor_service}:{cadvisor.get('internal_port', 8080)}"]}],
            },
        ],
    }
