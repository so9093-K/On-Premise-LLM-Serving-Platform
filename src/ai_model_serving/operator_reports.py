from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_model_serving.domain import ModelRegistry


def runtime_targets_document(registry: ModelRegistry) -> dict[str, Any]:
    """Build an operator-facing runtime target inventory from registry projections."""
    return {
        "version": str(registry.catalog.get("version", "")),
        "source_of_truth": ["configs/model_catalog.yaml", "configs/model_serving.yaml"],
        "model_list_schema": {
            "model_ids": list(registry.model_list_schema_projection().model_ids),
            "capability_values": list(registry.model_list_schema_projection().capability_values),
        },
        "runtime_targets": [target.as_dict() for target in registry.runtime_validation_targets()],
        "monitoring_labels": [target.as_dict() for target in registry.monitoring_targets()],
        "compose_service_regex": registry.monitoring_compose_service_regex(),
    }


def runtime_targets_markdown(document: dict[str, Any]) -> str:
    """Render the runtime target inventory as a compact Markdown report."""
    lines = [
        "# 런타임 대상 인벤토리",
        "",
        f"버전: `{document.get('version', '')}`",
        "",
        "원천 파일: `configs/model_catalog.yaml` + `configs/model_serving.yaml`",
        "",
        "## 런타임 대상",
        "",
        "| 서비스 키 | 모델 | 제공 모델명 | 포트 | Compose 서비스 | 기능 |",
        "|---|---|---|---:|---|---|",
    ]
    for target in document.get("runtime_targets", []):
        capabilities = ", ".join(target.get("capabilities", []))
        row = dict(target)
        row["capabilities_text"] = capabilities
        lines.append(
            "| {service_key} | {logical_id} | {served_model_name} | {port} | {compose_service_name} | {capabilities_text} |".format(
                **row
            )
        )
    lines.extend([
        "",
        "## 모니터링 라벨 계약",
        "",
        "| 서비스 키 | model 라벨 | runtime_service 라벨 | 포트 |",
        "|---|---|---|---:|",
    ])
    for target in document.get("monitoring_labels", []):
        lines.append(
            "| {service_key} | {model} | {runtime_service} | {port} |".format(**target)
        )
    schema = document.get("model_list_schema", {})
    lines.extend([
        "",
        "## 모델 목록 스키마 projection",
        "",
        f"모델 ID: `{', '.join(schema.get('model_ids', []))}`",
        f"기능 값: `{', '.join(schema.get('capability_values', []))}`",
        "",
        f"Compose 서비스 정규식: `{document.get('compose_service_regex', '')}`",
        "",
        "## 운영 해석",
        "",
        "이 리포트는 ModelRegistry가 실제 runtime target, compose service, monitoring label과 어떻게 연결되는지 보여준다. 모델이 추가되거나 제거되면 이 목록과 model list schema, runtime validation matrix, monitoring projection이 함께 바뀌어야 한다.",
        "",
        "이 리포트에는 원문 프롬프트, 사용자 텍스트, 모델 출력, Authorization 헤더, secret 값을 포함하지 않는다.",
    ])
    return "\n".join(lines) + "\n"


def write_runtime_targets_report(registry: ModelRegistry, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    document = runtime_targets_document(registry)
    json_path = output_dir / "runtime_targets.json"
    md_path = output_dir / "runtime_targets.md"
    json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(runtime_targets_markdown(document), encoding="utf-8")
    return json_path, md_path
