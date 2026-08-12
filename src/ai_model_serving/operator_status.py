from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_model_serving import status as status_vocab
from ai_model_serving.domain import ModelRegistry
from ai_model_serving.monitoring_projection import monitoring_projection_document
from ai_model_serving.operator_reports import runtime_targets_document


def _reserve_hard_minimum(reserve_policy: dict[str, Any]) -> float | None:
    value = reserve_policy.get("hard_minimum")
    return float(value) if value is not None else None


def _gpu_budget_summary(gpu_budgets: dict[str, Any], registry: ModelRegistry) -> dict[str, Any]:
    """Build a compact GPU/resource budget view for operator status bundles."""
    runtime_services = registry.iter_runtime_services()
    total_utilization = round(
        sum(float(service.config.get("gpu_memory_utilization", 0)) for service in runtime_services),
        6,
    )
    utilization_policy = gpu_budgets.get("gpu", {}).get("total_gpu_memory_utilization", {})
    reserve_policy = gpu_budgets.get("gpu", {}).get("reserve_gib", {})
    return {
        "runtime_service_count": len(runtime_services),
        "total_gpu_memory_utilization": total_utilization,
        "avoid_above": utilization_policy.get("avoid_above"),
        "reserve_gib_hard_minimum": _reserve_hard_minimum(reserve_policy),
        "per_runtime": [
            {
                "service_key": service.service_key,
                "logical_id": service.logical_id,
                "compose_service_name": service.compose_service_name,
                "gpu_memory_utilization": service.config.get("gpu_memory_utilization"),
                "max_model_len": service.config.get("max_model_len"),
                "max_num_seqs": service.config.get("max_num_seqs"),
                "max_num_batched_tokens": service.config.get("max_num_batched_tokens"),
                "optimization_level": service.config.get("optimization_level", ""),
            }
            for service in runtime_services
        ],
    }


def operator_status_bundle_document(
    *,
    registry: ModelRegistry,
    monitoring: dict[str, Any],
    services: dict[str, Any],
    gpu_budgets: dict[str, Any],
    version: str,
) -> dict[str, Any]:
    """Build a single registry-backed operator status/control-plane bundle.

    The bundle intentionally contains only topology, contract, resource, and
    validation metadata. It must not include raw prompts, user text, model
    output, Authorization headers, or secret values.
    """
    runtime_targets = runtime_targets_document(registry)
    monitoring_projection = monitoring_projection_document(registry=registry, monitoring=monitoring, services=services)
    return {
        "version": version,
        "source_of_truth": [
            "configs/model_catalog.yaml",
            "configs/model_serving.yaml",
            "configs/gpu_budgets.yaml",
            "configs/monitoring.yaml",
            "configs/services.yaml",
            "src/ai_model_serving/status.py",
        ],
        "privacy_contract": {
            "raw_prompt_included": False,
            "user_text_included": False,
            "model_output_included": False,
            "authorization_header_included": False,
        },
        "readiness_vocabulary": {
            "statuses": list(status_vocab.READINESS_STATUSES),
            "dependency_statuses": list(status_vocab.DEPENDENCY_STATUSES),
            "phases": list(status_vocab.READINESS_PHASES),
        },
        "runtime_targets": runtime_targets["runtime_targets"],
        "monitoring_labels": runtime_targets["monitoring_labels"],
        "compose_service_regex": runtime_targets["compose_service_regex"],
        "model_list_schema": runtime_targets["model_list_schema"],
        "monitoring_projection": {
            "prometheus_scrape_jobs": [
                job.get("job_name", "")
                for job in monitoring_projection["prometheus_scrape_config"]["scrape_configs"]
            ],
            "grafana_variables": monitoring_projection["grafana_variables"],
            "recording_rule_contract": monitoring_projection["recording_rules"],
        },
        "model_inventory": [row.as_dict() for row in registry.inventory_rows()],
        "runtime_validation_matrix": registry.runtime_validation_matrix_document()["validation_checks"],
        "gpu_budget_summary": _gpu_budget_summary(gpu_budgets, registry),
        "monitoring_summary": {
            "model_labels": list(registry.monitoring_model_labels()),
            "runtime_service_labels": list(registry.monitoring_compose_service_labels()),
        },
        "operator_commands": {
            "runtime_targets": "make runtime-targets",
            "monitoring_projection": "make monitoring-projection",
            "operator_status": "make operator-status",
            "runtime_validate": "make runtime-validate",
            "live_evidence": "make live-evidence",
            "validate": "make validate",
            "status": "make status READY_MODE=full",
        },
    }


def operator_status_bundle_markdown(document: dict[str, Any]) -> str:
    """Render the operator status bundle as a human-readable Markdown report."""
    lines = [
        "<!-- GENERATED FILE. DO NOT EDIT.",
        "Source:",
        "- configs/model_catalog.yaml",
        "- configs/model_serving.yaml",
        "- configs/monitoring.yaml",
        "- configs/gpu_budgets.yaml",
        "Command:",
        "- make operator-status",
        "- make operator-reports",
        "-->",
        "",
        "# 운영 상태 번들",
        "",
        f"Package version: `{document.get('version', '')}`",
        "",
        "이 리포트는 registry/config projection에서 생성되며 원문 프롬프트, 사용자 텍스트, 모델 출력, Authorization 헤더, secret 값을 포함하지 않는다.",
        "",
        "## 런타임 대상",
        "",
        "| 서비스 키 | 모델 | 제공 모델명 | 포트 | Compose 서비스 | 기능 |",
        "|---|---|---|---:|---|---|",
    ]
    for target in document.get("runtime_targets", []):
        lines.append(
            "| {service_key} | {logical_id} | {served_model_name} | {port} | {compose_service_name} | {capabilities} |".format(
                service_key=target.get("service_key", ""),
                logical_id=target.get("logical_id", ""),
                served_model_name=target.get("served_model_name", ""),
                port=target.get("port", ""),
                compose_service_name=target.get("compose_service_name", ""),
                capabilities=", ".join(target.get("capabilities", [])),
            )
        )

    gpu = document.get("gpu_budget_summary", {})
    lines.extend([
        "",
        "## GPU/리소스 예산",
        "",
        f"설정된 GPU memory utilization 합계: `{gpu.get('total_gpu_memory_utilization', '')}`",
        f"초과 회피 기준: `{gpu.get('avoid_above', '')}`",
        f"reserve hard minimum GiB: `{gpu.get('reserve_gib_hard_minimum', '')}`",
        "",
        "| 서비스 키 | 모델 | Compose 서비스 | GPU utilization | max_model_len | max_num_batched_tokens | max_num_seqs | O level |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ])
    for row in gpu.get("per_runtime", []):
        lines.append(
            "| {service_key} | {logical_id} | {compose_service_name} | {gpu_memory_utilization} | {max_model_len} | {max_num_batched_tokens} | {max_num_seqs} | {optimization_level} |".format(**row)
        )

    readiness = document.get("readiness_vocabulary", {})
    monitoring = document.get("monitoring_summary", {})
    lines.extend([
        "",
        "## Readiness/status 용어",
        "",
        f"상태값: `{', '.join(readiness.get('statuses', []))}`",
        f"의존성 상태값: `{', '.join(readiness.get('dependency_statuses', []))}`",
        f"단계값: `{', '.join(readiness.get('phases', []))}`",
        "",
        "## 모니터링 라벨",
        "",
        f"모델 라벨: `{', '.join(monitoring.get('model_labels', []))}`",
        f"런타임 서비스 라벨: `{', '.join(monitoring.get('runtime_service_labels', []))}`",
        f"Compose 서비스 정규식: `{document.get('compose_service_regex', '')}`",
        f"Prometheus scrape job: `{', '.join(document.get('monitoring_projection', {}).get('prometheus_scrape_jobs', []))}`",
        "",
        "",
        "## 런타임 검증 matrix",
        "",
        "| 검증 | 담당 | 산출물 | Runtime 필요 |",
        "|---|---|---|---|",
    ])
    for check in document.get("runtime_validation_matrix", []):
        lines.append(
            "| {id} | {owner} | {artifact_file} | {runtime_validation_required} |".format(**check)
        )

    commands = document.get("operator_commands", {})
    lines.extend([
        "",
        "## 운영 명령",
        "",
        f"- 런타임 대상: `{commands.get('runtime_targets', '')}`",
        f"- 모니터링 projection: `{commands.get('monitoring_projection', '')}`",
        f"- 운영 상태 번들: `{commands.get('operator_status', '')}`",
        f"- Live runtime 검증: `{commands.get('runtime_validate', '')}`",
        f"- Live evidence 번들: `{commands.get('live_evidence', '')}`",
        f"- 실행 전 정적 검증: `{commands.get('validate', '')}`",
        f"- 전체 상태 확인: `{commands.get('status', '')}`",
        "",
        "## 운영 해석",
        "",
        "이 번들은 현재 모델 registry, GPU budget, monitoring label, readiness vocabulary를 한 번에 보는 운영자용 상태판이다. 장애 대응 시에는 먼저 이 파일에서 어떤 runtime service와 모델 ID가 기대 상태인지 확인하고, live 검증이 필요하면 `make runtime-validate`를 실행한 뒤 `make operator-reports`로 evidence를 갱신한다.",
        "",
        "모델 추가·제거는 단일 YAML 수정으로 끝나지 않는다. catalog, serving config, contract, registry 기반 runtime validation plan, monitoring projection, test를 함께 갱신하고 필요한 모델 카드는 운영 문서로 검토해야 하므로 현재 `modelctl`은 read-only 검증에 머문다.",
        "",
        "운영자가 이 파일에서 먼저 확인할 항목은 세 가지다. 첫째, 모델 ID와 runtime service 이름이 기대와 같은지 본다. 둘째, GPU utilization 합계가 회피 기준을 넘지 않는지 본다. 셋째, monitoring label과 readiness vocabulary가 dashboard와 정적 검증에서 같은 의미로 쓰이는지 본다. 이 세 가지가 맞지 않으면 full-stack을 올리기 전에 정적 설정을 먼저 고친다.",
    ])
    return "\n".join(lines) + "\n"


def write_operator_status_bundle(document: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "operator_status_bundle.json"
    md_path = output_dir / "operator_status_bundle.md"
    json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(operator_status_bundle_markdown(document), encoding="utf-8")
    return json_path, md_path
