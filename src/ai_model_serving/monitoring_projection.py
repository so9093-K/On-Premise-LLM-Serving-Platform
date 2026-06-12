from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_model_serving.domain import ModelRegistry


def prometheus_scrape_config_document(*, registry: ModelRegistry, monitoring: dict[str, Any]) -> dict[str, Any]:
    """Build the reference Prometheus scrape config from registry/config projections.

    This is intentionally a static projection for the reference compose topology.
    It contains endpoint/label metadata only and never includes prompts, user text,
    model outputs, Authorization headers, or secret values.
    """
    stack = monitoring.get("monitoring_stack", {})
    metric_sources = monitoring.get("metric_sources", {})
    gateway = metric_sources.get("gateway", {})
    risk = metric_sources.get("risk_adapter", {})
    vllm = metric_sources.get("vllm_instances", {})
    dcgm = stack.get("dcgm_exporter", {})
    cadvisor = stack.get("cadvisor", {})
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
                "static_configs": [{"targets": [f"gateway:{gateway.get('public_port', 9400)}"]}],
            },
            {
                "job_name": "risk-adapter",
                "metrics_path": risk.get("metrics_path", "/metrics"),
                "bearer_token_file": "/run/secrets/admin_api_key",
                "static_configs": [{"targets": [f"risk-adapter:{risk.get('public_port', 9405)}"]}],
            },
            {
                "job_name": vllm.get("scrape_job", "vllm-runtimes"),
                "metrics_path": vllm.get("metrics_path", "/metrics"),
                "static_configs": static_vllm_configs,
            },
            {
                "job_name": "dcgm-exporter",
                "metrics_path": dcgm.get("default_metrics_path", "/metrics"),
                "static_configs": [{"targets": [f"dcgm-exporter:{dcgm.get('internal_port', 9400)}"]}],
            },
            {
                "job_name": "cadvisor",
                "metrics_path": cadvisor.get("default_metrics_path", "/metrics"),
                "static_configs": [{"targets": [f"cadvisor:{cadvisor.get('internal_port', 8080)}"]}],
            },
        ],
    }


def recording_rule_projection(*, registry: ModelRegistry) -> dict[str, Any]:
    """Return registry-derived recording-rule expectations for dashboards."""
    compose_regex = registry.monitoring_compose_service_regex()
    return {
        "group": "ai_model_serving_model_runtime",
        "compose_service_label": "container_label_com_docker_compose_service",
        "compose_service_regex": compose_regex,
        "required_records": [
            "model_runtime_upstream_error_rate_5m",
            "model_runtime_upstream_p95_latency_seconds",
            "model_runtime_http_p95_latency_seconds",
            "model_runtime_gateway_ready",
            "gpu_memory_used_bytes",
            "gpu_memory_total_bytes",
            "gpu_memory_headroom_bytes",
            "gpu_utilization_ratio",
            "gpu_temperature_celsius",
            "gpu_power_watts",
            "vllm_kv_cache_usage_ratio",
            "vllm_queue_depth",
            "vllm_token_throughput_per_second",
            "vllm_container_memory_usage_bytes",
            "vllm_container_cpu_cores_used",
        ],
        "cadvisor_bridge_expressions": {
            "vllm_container_memory_usage_bytes": (
                'max by (container_label_com_docker_compose_service) '
                f'(container_memory_usage_bytes{{container_label_com_docker_compose_service=~"{compose_regex}"}})'
            ),
            "vllm_container_cpu_cores_used": (
                'sum by (container_label_com_docker_compose_service) '
                f'(rate(container_cpu_usage_seconds_total{{container_label_com_docker_compose_service=~"{compose_regex}"}}[1m]))'
            ),
        },
    }


def observability_trust_projection(*, registry: ModelRegistry) -> dict[str, Any]:
    """Return scrape target expectations used by the Serving Home verdict banner."""
    return {
        "critical_jobs": ["gateway", "risk-adapter", "vllm-runtimes", "dcgm-exporter", "cadvisor"],
        "expected_critical_target_count": 1 + 1 + len(registry.runtime_validation_targets()) + 1 + 1,
        "count_formula": "gateway + risk-adapter + vLLM runtime targets + dcgm-exporter + cadvisor",
    }


def container_signal_projection(*, registry: ModelRegistry, monitoring: dict[str, Any]) -> dict[str, Any]:
    """Return cAdvisor container signal expectations for OOM/restart panels."""
    config = monitoring.get("metric_sources", {}).get("container_signal_sources", {})
    vllm_regex = registry.monitoring_compose_service_regex()
    default_critical_regex = f"gateway|risk-adapter|{vllm_regex}"
    return {
        "compose_service_label": str(config.get("compose_service_label", "container_label_com_docker_compose_service")),
        "critical_container_signal_regex": str(config.get("critical_container_regex", default_critical_regex)),
        "vllm_container_regex": str(config.get("vllm_container_regex", vllm_regex)),
        "source_metrics": list(config.get("source_metrics", ["container_oom_events_total", "container_start_time_seconds"])),
    }


def grafana_variable_projection(*, registry: ModelRegistry, monitoring: dict[str, Any]) -> dict[str, Any]:
    """Return dashboard variable values operators expect to see."""
    dashboards = monitoring.get("ux_dashboards", [])
    grafana = monitoring.get("monitoring_stack", {}).get("grafana", {})
    return {
        "datasource_uid": str(grafana.get("provisioned_datasource_uid", "prometheus")),
        "variable_names": list(grafana.get("dashboard_variables", [])),
        "variable_scope": str(grafana.get("dashboard_variable_scope", "")),
        "window_values": ["1m", "5m", "15m", "1h"],
        "model_values": list(registry.monitoring_model_labels()),
        "runtime_service_values": list(registry.monitoring_compose_service_labels()),
        "landing_dashboard": monitoring.get("operator_status_ux", {}).get("landing_dashboard", ""),
        "dashboard_ids": [str(item.get("id", "")) for item in dashboards if item.get("id")],
        "status_levels": [str(item.get("level", "")) for item in monitoring.get("operator_status_ux", {}).get("status_levels", [])],
    }


def monitoring_projection_document(*, registry: ModelRegistry, monitoring: dict[str, Any]) -> dict[str, Any]:
    """Build a consolidated monitoring projection for operators and governance."""
    return {
        "version": str(monitoring.get("version", registry.catalog.get("version", ""))),
        "source_of_truth": [
            "configs/model_catalog.yaml",
            "configs/model_serving.yaml",
            "configs/monitoring.yaml",
            "src/ai_model_serving/domain/model_registry.py",
        ],
        "privacy_contract": {
            "raw_prompt_included": False,
            "user_text_included": False,
            "model_output_included": False,
            "authorization_header_included": False,
        },
        "prometheus_scrape_config": prometheus_scrape_config_document(registry=registry, monitoring=monitoring),
        "recording_rules": recording_rule_projection(registry=registry),
        "observability_trust": observability_trust_projection(registry=registry),
        "container_signals": container_signal_projection(registry=registry, monitoring=monitoring),
        "grafana_variables": grafana_variable_projection(registry=registry, monitoring=monitoring),
    }


def monitoring_projection_markdown(document: dict[str, Any]) -> str:
    """Render the monitoring projection as a compact operator-facing Markdown report."""
    scrape = document.get("prometheus_scrape_config", {})
    jobs = scrape.get("scrape_configs", [])
    variables = document.get("grafana_variables", {})
    rules = document.get("recording_rules", {})
    trust = document.get("observability_trust", {})
    container_signals = document.get("container_signals", {})
    lines = [
        "<!-- GENERATED FILE. DO NOT EDIT.",
        "Source:",
        "- configs/model_catalog.yaml",
        "- configs/monitoring.yaml",
        "Command:",
        "- make monitoring-projection",
        "- make operator-reports",
        "-->",
        "",
        "# 모니터링 Projection",
        "",
        f"Schema version: `{document.get('version', '')}`",
        "",
        "이 리포트는 registry/config projection에서 생성되며 원문 프롬프트, 사용자 텍스트, 모델 출력, Authorization 헤더, secret 값을 포함하지 않는다.",
        "",
        "## Prometheus scrape job",
        "",
        "| Job | Metrics path | 대상 | 라벨 |",
        "|---|---|---|---|",
    ]
    for job in jobs:
        labels: list[str] = []
        targets: list[str] = []
        for static in job.get("static_configs", []):
            targets.extend(static.get("targets", []))
            if static.get("labels"):
                labels.append(", ".join(f"{key}={value}" for key, value in static.get("labels", {}).items()))
        lines.append(
            "| {job} | {path} | {targets} | {labels} |".format(
                job=job.get("job_name", ""),
                path=job.get("metrics_path", ""),
                targets=", ".join(targets),
                labels="; ".join(labels) or "-",
            )
        )
    lines.extend([
        "",
        "## Recording rule 계약",
        "",
        f"Compose 서비스 라벨: `{rules.get('compose_service_label', '')}`",
        f"Compose 서비스 정규식: `{rules.get('compose_service_regex', '')}`",
        f"필수 record: `{', '.join(rules.get('required_records', []))}`",
        "",
        "## Serving Home evidence",
        "",
        "이 섹션은 Serving Home 첫 화면의 scrape 신뢰도와 container OOM/restart signal이 어떤 기준에서 나온 값인지 설명한다. runtime target 수가 바뀌면 expected critical scrape target count와 dashboard query 검증도 함께 바뀐다.",
        "운영자는 이 값을 보고 idle 상태가 빈 화면인지, 정상 대기 상태인지, 또는 scrape 누락인지 구분한다. container signal regex는 Gateway, Risk Adapter, vLLM runtime 컨테이너를 함께 감시하기 위한 기준이다.",
        f"Expected critical scrape target count: `{trust.get('expected_critical_target_count', '')}`",
        f"Expected count formula: `{trust.get('count_formula', '')}`",
        f"Critical jobs: `{', '.join(trust.get('critical_jobs', []))}`",
        f"Container signal label: `{container_signals.get('compose_service_label', '')}`",
        f"Critical container signal regex: `{container_signals.get('critical_container_signal_regex', '')}`",
        f"vLLM container regex: `{container_signals.get('vllm_container_regex', '')}`",
        f"Container signal source metrics: `{', '.join(container_signals.get('source_metrics', []))}`",
        "",
        "## Grafana 변수",
        "",
        f"Datasource UID: `{variables.get('datasource_uid', '')}`",
        f"Variable 이름: `{', '.join(variables.get('variable_names', []))}`",
        f"Variable scope: `{variables.get('variable_scope', '')}`",
        f"Window 값: `{', '.join(variables.get('window_values', []))}`",
        f"모델 값: `{', '.join(variables.get('model_values', []))}`",
        f"런타임 서비스 값: `{', '.join(variables.get('runtime_service_values', []))}`",
        f"첫 화면 dashboard: `{variables.get('landing_dashboard', '')}`",
        f"Dashboard ID: `{', '.join(variables.get('dashboard_ids', []))}`",
        "",
        "## 운영 해석",
        "",
        "이 리포트는 모니터링 설정을 사람이 직접 맞추는 부담을 줄이기 위한 projection이다. 모델이 추가되거나 compose service 이름이 바뀌면 Prometheus scrape job, Grafana variable, recording rule 정규식이 함께 바뀌어야 한다. 값이 어긋나면 dashboard가 비어 보이거나 특정 runtime 장애를 놓칠 수 있으므로 `make monitoring-projection`과 `make operator-reports`를 함께 실행한다.",
        "",
        "보안상 이 문서는 topology와 label만 담고, prompt·사용자 입력·모델 출력·Authorization header·secret 값은 담지 않는다.",
    ])
    return "\n".join(lines) + "\n"


def write_monitoring_projection_report(document: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "monitoring_projection.json"
    md_path = output_dir / "monitoring_projection.md"
    json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(monitoring_projection_markdown(document), encoding="utf-8")
    return json_path, md_path
