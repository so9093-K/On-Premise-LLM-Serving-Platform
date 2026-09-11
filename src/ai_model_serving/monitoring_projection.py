from __future__ import annotations

from typing import Any

from ai_model_serving.domain import ModelRegistry


def macos_metal_prometheus_config_document(
    *,
    monitoring: dict[str, Any],
    services: dict[str, Any],
    macos_runtime: dict[str, Any],
    runtime_backend: str,
) -> dict[str, Any]:
    """Build the Prometheus configuration for the static Metal Compose project.

    rule_files를 싣지 않는 것은 의도다. model_runtime.rules.yml의 rule은 전부
    DCGM/cAdvisor/vLLM exporter를 전제하는데 이 project는 그 service들을 정의하지
    않는다. 규칙을 실어도 series가 전부 absent라 얻는 것이 없다.
    """
    stack = monitoring.get("monitoring_stack", {})
    prometheus = stack.get("prometheus", {})
    exporter = stack.get("mlx_metrics_exporter", {})
    gateway = monitoring.get("metric_sources", {}).get("gateway", {})
    gateway_service = str(services["gateway"]["compose_service"])
    return {
        "global": {
            "scrape_interval": prometheus.get("scrape_interval", "15s"),
            "scrape_timeout": prometheus.get("scrape_timeout", "10s"),
        },
        "scrape_configs": [
            {
                "job_name": "gateway",
                "metrics_path": gateway.get("metrics_path", "/metrics"),
                # ADMIN_API_KEY_REQUIRED=true이면 Gateway /metrics가 admin token을
                # 요구한다. Linux full-stack과 같은 compose secret을 사용한다.
                "bearer_token_file": "/run/secrets/admin_api_key",
                "static_configs": [
                    {"targets": [f"{gateway_service}:{services['gateway']['container_port']}"]}
                ],
            },
            {
                "job_name": "mlx-runtime",
                "metrics_path": exporter.get("default_metrics_path", "/metrics"),
                "static_configs": [
                    {
                        "targets": [
                            f"{exporter['compose_service']}:{exporter['internal_port']}"
                        ],
                        "labels": {
                            "model": str(macos_runtime["public_model"]),
                            "runtime_backend": runtime_backend,
                        },
                    }
                ],
            },
        ],
    }


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
                "static_configs": [
                    {"targets": [f"{dcgm_service}:{services['dcgm_exporter']['container_port']}"]}
                ],
            },
            {
                "job_name": "cadvisor",
                "metrics_path": cadvisor.get("default_metrics_path", "/metrics"),
                "static_configs": [
                    {"targets": [f"{cadvisor_service}:{services['cadvisor']['container_port']}"]}
                ],
            },
        ],
    }
