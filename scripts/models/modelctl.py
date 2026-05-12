#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from scripts.lib.cli_kr import KoreanArgumentParser  # noqa: E402
from ai_model_serving.domain import ModelRegistry  # noqa: E402


MODEL_LIFECYCLE_FILES = [
    "configs/model_catalog.yaml",
    "configs/model_serving.yaml",
    "model_cards/<model-id>.json",
    "contracts/model_contracts.yaml",
    "specs/schemas/model_list_response.schema.json",
    "harness/runtime_validation_matrix.yaml",
    "ops/compose/full-stack.private-network.yaml",
    "ops/prometheus/prometheus.yml",
    "reports/runtime/runtime_targets.*",
    "reports/runtime/monitoring_projection.*",
]


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_registry(root: Path) -> ModelRegistry:
    return ModelRegistry(
        load_yaml(root / "configs/model_catalog.yaml"),
        load_yaml(root / "configs/model_serving.yaml"),
    )


def gpu_summary(root: Path, registry: ModelRegistry) -> dict[str, Any]:
    budgets = load_yaml(root / "configs/gpu_budgets.yaml")
    total = round(sum(float(service.config.get("gpu_memory_utilization", 0)) for service in registry.iter_runtime_services()), 6)
    policy = budgets["gpu"]["total_gpu_memory_utilization"]
    return {
        "profile": budgets["gpu"].get("default_profile"),
        "total_gpu_memory_utilization": total,
        "recommended_start": policy.get("recommended_start"),
        "avoid_above": policy.get("avoid_above"),
        "over_avoid_threshold": total > float(policy.get("avoid_above", 1.0)),
    }


def model_rows(registry: ModelRegistry) -> list[dict[str, Any]]:
    service_by_id = {service.logical_id: service for service in registry.iter_runtime_services() if service.logical_id}
    rows: list[dict[str, Any]] = []
    for record in registry.iter_records():
        service = service_by_id.get(record.logical_id)
        rows.append({
            "id": record.logical_id,
            "role": record.role,
            "state": record.lifecycle_state,
            "exposure": record.exposure,
            "public": record.public_enabled,
            "runtime_service": service.service_key if service else None,
            "runtime": service.backend if service else record.backend,
            "port": record.port,
            "endpoint": record.endpoint_path,
            "upstream_model_id": record.upstream_model_id,
            "capabilities": list(record.capabilities),
        })
    return rows


def status_document(root: Path, registry: ModelRegistry) -> dict[str, Any]:
    rows = model_rows(registry)
    states: dict[str, int] = {}
    for row in rows:
        states[row["state"]] = states.get(row["state"], 0) + 1
    issues = [issue.__dict__ for issue in registry.alignment_issues()]
    return {
        "source_of_truth": ["configs/model_catalog.yaml", "configs/model_serving.yaml"],
        "model_count": len(rows),
        "public_model_count": sum(1 for row in rows if row["public"]),
        "lifecycle_states": states,
        "gpu": gpu_summary(root, registry),
        "alignment_issues": issues,
        "models": rows,
    }


def projection_diffs(root: Path, registry: ModelRegistry) -> list[dict[str, Any]]:
    checks: list[tuple[str, Any, Any]] = []
    checks.append((
        "contracts/model_contracts.yaml",
        load_yaml(root / "contracts/model_contracts.yaml"),
        registry.model_contracts_document(),
    ))
    checks.append((
        "specs/schemas/model_list_response.schema.json",
        load_json(root / "specs/schemas/model_list_response.schema.json"),
        registry.model_list_schema_document(),
    ))
    matrix_path = root / "harness/runtime_validation_matrix.yaml"
    if matrix_path.exists():
        checks.append((
            "harness/runtime_validation_matrix.yaml",
            load_yaml(matrix_path),
            registry.runtime_validation_matrix_document(),
        ))
    diffs = []
    for path, actual, expected in checks:
        diffs.append({"path": path, "status": "ok" if actual == expected else "diff"})
    return diffs


def validate_document(root: Path, registry: ModelRegistry) -> dict[str, Any]:
    issues = [issue.__dict__ for issue in registry.alignment_issues()]
    diffs = projection_diffs(root, registry)
    gpu = gpu_summary(root, registry)
    lifecycle_errors: list[dict[str, str]] = []
    for row in model_rows(registry):
        if row["state"] not in {"experimental", "active", "deprecated", "disabled", "retired", "removed"}:
            lifecycle_errors.append({"model": row["id"], "error": f"unknown lifecycle state {row['state']}"})
        if row["exposure"] not in {"public", "internal", "hidden"}:
            lifecycle_errors.append({"model": row["id"], "error": f"unknown exposure {row['exposure']}"})
    return {
        "ok": not issues and not lifecycle_errors and all(item["status"] == "ok" for item in diffs) and not gpu["over_avoid_threshold"],
        "alignment_issues": issues,
        "projection_diffs": diffs,
        "lifecycle_errors": lifecycle_errors,
        "gpu": gpu,
    }


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def propose_add_document(args: argparse.Namespace, root: Path, registry: ModelRegistry) -> dict[str, Any]:
    rows = model_rows(registry)
    existing_ids = {row["id"] for row in rows}
    existing_ports = {int(row["port"]) for row in rows if row.get("port") is not None}
    existing_endpoints = {str(row["endpoint"]) for row in rows if row.get("endpoint")}
    service_keys = {service.service_key for service in registry.iter_runtime_services()}
    capabilities = _split_csv(args.capabilities) or [args.capability]
    requested = {
        "id": args.id,
        "role": args.role,
        "upstream_model_id": args.upstream_model_id,
        "served_model_name": args.served_model_name or args.id,
        "runtime_service": args.runtime_service or args.id.replace("-", "_"),
        "backend": args.backend,
        "port": args.port,
        "endpoint": args.endpoint,
        "capabilities": capabilities,
        "lifecycle_state": args.state,
        "exposure": args.exposure,
        "gpu_memory_utilization": args.gpu_memory_utilization,
    }
    blockers: list[str] = []
    warnings: list[str] = []
    if args.id in existing_ids:
        blockers.append(f"이미 존재하는 model id입니다: {args.id}")
    if args.port in existing_ports:
        blockers.append(f"이미 사용 중인 runtime port입니다: {args.port}")
    if args.endpoint in existing_endpoints:
        blockers.append(f"이미 사용 중인 public/internal endpoint입니다: {args.endpoint}")
    if requested["runtime_service"] in service_keys:
        blockers.append(f"이미 존재하는 runtime service key입니다: {requested['runtime_service']}")
    gpu = gpu_summary(root, registry)
    projected_gpu = None
    if args.gpu_memory_utilization is None:
        warnings.append("GPU memory utilization을 지정하지 않았습니다. 실제 추가 전 capacity plan이 필요합니다.")
    else:
        projected_gpu = round(float(gpu["total_gpu_memory_utilization"]) + float(args.gpu_memory_utilization), 6)
        if projected_gpu > float(gpu["avoid_above"]):
            warnings.append(f"추가 후 GPU 예산이 회피 기준을 초과할 수 있습니다: {projected_gpu} > {gpu['avoid_above']}")
    steps = [
        "configs/model_catalog.yaml에 logical model, lifecycle, gateway_listing, runtime 정책을 추가",
        "configs/model_serving.yaml에 runtime service stanza와 resource_control을 추가",
        "model_cards/<model-id>.json을 catalog projection과 맞춰 추가",
        "python scripts/models/modelctl.py validate/diff로 registry projection 확인",
        "python scripts/validation/runtime_validation.py --config-only로 runtime matrix 확인",
        "make operator-reports로 runtime targets/monitoring/project inventory 재생성",
        "Docker/GPU 서버에서 readiness와 vLLM smoke를 확인",
    ]
    return {
        "action": "propose-add",
        "status": "blocked" if blockers else "plan-only",
        "writes_files": False,
        "requested_model": requested,
        "current_gpu": gpu,
        "projected_gpu_memory_utilization": projected_gpu,
        "affected_files": MODEL_LIFECYCLE_FILES,
        "blockers": blockers,
        "warnings": warnings,
        "steps": steps,
    }


def propose_remove_document(model_id: str, root: Path, registry: ModelRegistry) -> dict[str, Any]:
    rows = {row["id"]: row for row in model_rows(registry)}
    blockers: list[str] = []
    if model_id not in rows:
        blockers.append(f"존재하지 않는 model id입니다: {model_id}")
    row = rows.get(model_id)
    steps = [
        "1단계: lifecycle.state=deprecated로 전환하고 deprecation_deadline/removal_ticket을 기록",
        "2단계: gateway_listing.enabled=false 또는 lifecycle.exposure=hidden으로 public listing에서 제거",
        "3단계: 운영 공지와 client migration window를 끝낸 뒤 runtime service를 제거",
        "4단계: model card를 archive하거나 removed 상태로 남겨 감사 추적을 보존",
        "5단계: modelctl validate/diff, runtime_validation --config-only, operator-reports를 재실행",
        "6단계: 실제 Docker/GPU 서버에서 readiness, monitoring label, dashboard variable을 확인",
    ]
    warnings = [
        "write-mode 삭제가 아니라 plan-only입니다. 모델 제거는 단계적 deprecate/remove 절차로 진행해야 합니다.",
    ]
    if row and row.get("public"):
        warnings.append("현재 public listing에 노출된 모델입니다. 즉시 삭제 대신 deprecation window를 두세요.")
    return {
        "action": "propose-remove",
        "status": "blocked" if blockers else "plan-only",
        "writes_files": False,
        "model_id": model_id,
        "current_model": row,
        "affected_files": MODEL_LIFECYCLE_FILES,
        "blockers": blockers,
        "warnings": warnings,
        "steps": steps,
    }


def render_plan_document(doc: dict[str, Any]) -> str:
    lines = [
        f"모델 변경 계획: {doc['action']}",
        f"상태: {doc['status']}",
        "파일 쓰기: 없음(plan-only)",
        "",
    ]
    if doc.get("requested_model"):
        lines.append("요청 모델:")
        for key, value in doc["requested_model"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")
    if doc.get("current_model"):
        lines.append("현재 모델:")
        for key, value in doc["current_model"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")
    if doc.get("blockers"):
        lines.append("차단 조건:")
        lines.extend(f"- {item}" for item in doc["blockers"])
        lines.append("")
    if doc.get("warnings"):
        lines.append("주의:")
        lines.extend(f"- {item}" for item in doc["warnings"])
        lines.append("")
    lines.append("영향 파일:")
    lines.extend(f"- {item}" for item in doc["affected_files"])
    lines.append("")
    lines.append("권장 절차:")
    lines.extend(f"- {item}" for item in doc["steps"])
    return "\n".join(lines) + "\n"


def _model_change_slug(doc: dict[str, Any]) -> str:
    model_id = doc.get("model_id") or doc.get("requested_model", {}).get("id") or "unknown-model"
    return f"{doc['action']}-{str(model_id).replace('/', '_')}"


def render_patch_scaffold(doc: dict[str, Any]) -> str:
    """Return a human-reviewable patch checklist without modifying source files."""
    slug = _model_change_slug(doc)
    lines = [
        f"# Patch scaffold: {slug}",
        "",
        "이 파일은 적용용 patch가 아니라 운영자가 검토할 변경 초안입니다.",
        "source 파일은 자동 수정하지 않으며, 변경 전후에 `modelctl validate`, `modelctl diff`, `runtime_validation --config-only`를 실행하세요.",
        "",
        "## 영향 파일",
    ]
    lines.extend(f"- `{item}`" for item in doc.get("affected_files", []))
    lines.append("")
    if doc.get("requested_model"):
        requested = doc["requested_model"]
        runtime_service = requested["runtime_service"]
        lines.extend([
            "## configs/model_catalog.yaml 후보 항목",
            "```yaml",
            f"{requested['id']}:",
            f"  role: {requested['role']}",
            f"  upstream_model_id: {requested['upstream_model_id']}",
            "  lifecycle:",
            f"    state: {requested['lifecycle_state']}",
            f"    exposure: {requested['exposure']}",
            "    owner: platform",
            "  gateway_listing:",
            f"    enabled: {str(requested['exposure'] == 'public').lower()}",
            "    backend: vllm",
            "    capabilities:",
            *[f"    - {capability}" for capability in requested.get("capabilities", [])],
            "  runtime:",
            f"    served_model_name: {requested['served_model_name']}",
            f"    backend: {requested['backend']}",
            f"    port: {requested['port']}",
            f"    endpoint: {requested['endpoint']}",
            "```",
            "",
            "## configs/model_serving.yaml 후보 항목",
            "```yaml",
            f"{runtime_service}:",
            f"  name: {requested['upstream_model_id']}",
            f"  served_model_name: {requested['served_model_name']}",
            f"  backend: {requested['backend']}",
            f"  port: {requested['port']}",
            f"  endpoint: http://{runtime_service.replace('_', '-')}-vllm:{requested['port']}/v1",
            "  # TODO: max_model_len, max_num_seqs, gpu_memory_utilization, resource_control을 capacity plan에 맞춰 채우세요.",
            "```",
            "",
            "## model_cards 후보",
            f"- `model_cards/{requested['id']}.json`를 catalog projection에 맞춰 추가하세요.",
        ])
    elif doc.get("current_model"):
        row = doc["current_model"]
        lines.extend([
            "## 단계적 제거 후보",
            "```yaml",
            f"{doc['model_id']}:",
            "  lifecycle:",
            "    state: deprecated",
            "    exposure: hidden",
            "    deprecation_deadline: YYYY-MM-DD",
            "    removal_ticket: TICKET-ID",
            "  gateway_listing:",
            "    enabled: false",
            "```",
            "",
            f"현재 runtime service: `{row.get('runtime_service')}`",
            "runtime service 삭제는 client migration window가 끝난 뒤 별도 PR에서 진행하세요.",
        ])
    return "\n".join(lines) + "\n"


def _display_artifact_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def write_plan_artifacts(doc: dict[str, Any], root: Path, output_dir: str, *, include_patch: bool) -> list[str]:
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _model_change_slug(doc)
    json_path = out_dir / f"{slug}.plan.json"
    md_path = out_dir / f"{slug}.plan.md"
    json_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_plan_document(doc), encoding="utf-8")
    written = [_display_artifact_path(json_path, root), _display_artifact_path(md_path, root)]
    if include_patch:
        patch_path = out_dir / f"{slug}.patch-scaffold.md"
        patch_path.write_text(render_patch_scaffold(doc), encoding="utf-8")
        written.append(_display_artifact_path(patch_path, root))
    return written


def cmd_propose_add(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    registry = load_registry(root)
    doc = propose_add_document(args, root, registry)
    written: list[str] = []
    if args.write_plan or args.write_patch:
        written = write_plan_artifacts(doc, root, args.output_dir, include_patch=args.write_patch)
        doc["artifact_writes"] = written
    if args.format == "json":
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        print(render_plan_document(doc), end="")
        if written:
            print("생성된 artifact:")
            for path in written:
                print(f"- {path}")
    return 0 if not doc["blockers"] else 1


def cmd_propose_remove(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    registry = load_registry(root)
    doc = propose_remove_document(args.model_id, root, registry)
    written: list[str] = []
    if args.write_plan or args.write_patch:
        written = write_plan_artifacts(doc, root, args.output_dir, include_patch=args.write_patch)
        doc["artifact_writes"] = written
    if args.format == "json":
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        print(render_plan_document(doc), end="")
        if written:
            print("생성된 artifact:")
            for path in written:
                print(f"- {path}")
    return 0 if not doc["blockers"] else 1


def print_table(rows: list[dict[str, Any]]) -> None:
    headers = ["id", "role", "state", "exposure", "runtime_service", "runtime", "port", "endpoint"]
    labels = {
        "id": "모델 ID",
        "role": "역할",
        "state": "상태",
        "exposure": "노출",
        "runtime_service": "런타임 서비스",
        "runtime": "런타임",
        "port": "포트",
        "endpoint": "endpoint",
    }
    widths = {key: max(len(labels[key]), *(len(str(row.get(key, ""))) for row in rows)) for key in headers}
    print(" | ".join(labels[key].ljust(widths[key]) for key in headers))
    print("-+-".join("-" * widths[key] for key in headers))
    for row in rows:
        print(" | ".join(str(row.get(key, "")).ljust(widths[key]) for key in headers))


def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    registry = load_registry(root)
    rows = model_rows(registry)
    if args.format == "json":
        print(json.dumps({"models": rows}, indent=2, ensure_ascii=False))
    else:
        print_table(rows)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    registry = load_registry(root)
    doc = status_document(root, registry)
    if args.format == "json":
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        print(f"모델: 전체 {doc['model_count']}개 / public {doc['public_model_count']}개")
        print(f"lifecycle: {doc['lifecycle_states']}")
        gpu = doc["gpu"]
        print(f"GPU 예산: {gpu['total_gpu_memory_utilization']} / 회피 기준 {gpu['avoid_above']} ({gpu['profile']})")
        if doc["alignment_issues"]:
            print("정렬 문제:")
            for issue in doc["alignment_issues"]:
                print(f"- {issue['code']}: {issue['message']}")
        else:
            print("정렬 상태: 정상")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    registry = load_registry(root)
    doc = validate_document(root, registry)
    if args.format == "json":
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        print("모델 registry 검증: " + ("PASS" if doc["ok"] else "FAIL"))
        printable_sections = {
            "alignment_issues": doc["alignment_issues"],
            "projection_diffs": [item for item in doc["projection_diffs"] if item["status"] != "ok"],
            "lifecycle_errors": doc["lifecycle_errors"],
        }
        for section, items in printable_sections.items():
            if not items:
                continue
            print(section + ":")
            for item in items:
                print(f"- {item}")
        gpu = doc["gpu"]
        if gpu["over_avoid_threshold"]:
            print(f"GPU 예산이 회피 기준을 초과했습니다: {gpu['total_gpu_memory_utilization']} > {gpu['avoid_above']}")
    return 0 if doc["ok"] else 1


def cmd_diff(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    registry = load_registry(root)
    diffs = projection_diffs(root, registry)
    if args.format == "json":
        print(json.dumps({"projection_diffs": diffs}, indent=2, ensure_ascii=False))
    else:
        for item in diffs:
            print(f"{item['status'].upper():<4} {item['path']}")
    return 0 if all(item["status"] == "ok" for item in diffs) else 1


def build_parser() -> KoreanArgumentParser:
    parser = KoreanArgumentParser(description="운영자를 위한 model registry 제어 플레인입니다. 기본 명령은 읽기 전용이며 propose-*도 파일을 쓰지 않습니다.")
    parser.add_argument("--root", default=str(ROOT), help="프로젝트 root 경로입니다. 기본값은 현재 repository입니다.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in [("list", cmd_list), ("status", cmd_status), ("validate", cmd_validate), ("diff", cmd_diff)]:
        command = sub.add_parser(name, help=f"model registry {name} 결과를 표시합니다.")
        command.add_argument("--format", choices=["table", "json"], default="table", help="출력 형식입니다.")
        command.set_defaults(func=fn)

    add = sub.add_parser("propose-add", help="새 모델 추가 계획을 파일 쓰기 없이 생성합니다.")
    add.add_argument("--id", required=True, help="추가 후보 logical model id입니다.")
    add.add_argument("--role", required=True, help="모델 역할입니다. 예: main_llm, embedding, risk")
    add.add_argument("--upstream-model-id", required=True, help="HF 또는 upstream model id입니다.")
    add.add_argument("--served-model-name", help="vLLM served_model_name입니다. 기본값은 --id입니다.")
    add.add_argument("--runtime-service", help="model_serving.yaml runtime service key입니다. 기본값은 id의 '-'를 '_'로 바꾼 값입니다.")
    add.add_argument("--backend", default="vllm", help="runtime backend입니다.")
    add.add_argument("--port", required=True, type=int, help="후보 runtime port입니다.")
    add.add_argument("--endpoint", required=True, help="후보 public/internal endpoint path입니다.")
    add.add_argument("--capability", default="chat.completions", help="단일 capability입니다. --capabilities가 있으면 무시됩니다.")
    add.add_argument("--capabilities", help="쉼표로 구분한 capability 목록입니다.")
    add.add_argument("--state", default="experimental", choices=["experimental", "active", "deprecated", "disabled", "retired", "removed"], help="초기 lifecycle state입니다.")
    add.add_argument("--exposure", default="hidden", choices=["public", "internal", "hidden"], help="초기 exposure입니다.")
    add.add_argument("--gpu-memory-utilization", type=float, help="후보 GPU memory utilization입니다. 지정하면 예산 초과를 미리 경고합니다.")
    add.add_argument("--format", choices=["table", "json"], default="table", help="출력 형식입니다.")
    add.add_argument("--write-plan", action="store_true", help="reports/model_changes에 JSON/Markdown 계획 artifact를 저장합니다. source 파일은 수정하지 않습니다.")
    add.add_argument("--write-patch", action="store_true", help="계획 artifact와 함께 사람이 검토할 patch scaffold Markdown을 저장합니다.")
    add.add_argument("--output-dir", default="reports/model_changes", help="--write-plan/--write-patch artifact 출력 디렉터리입니다.")
    add.set_defaults(func=cmd_propose_add)

    remove = sub.add_parser("propose-remove", help="모델 제거 계획을 파일 쓰기 없이 생성합니다.")
    remove.add_argument("model_id", help="제거 후보 logical model id입니다.")
    remove.add_argument("--format", choices=["table", "json"], default="table", help="출력 형식입니다.")
    remove.add_argument("--write-plan", action="store_true", help="reports/model_changes에 JSON/Markdown 계획 artifact를 저장합니다. source 파일은 수정하지 않습니다.")
    remove.add_argument("--write-patch", action="store_true", help="계획 artifact와 함께 사람이 검토할 patch scaffold Markdown을 저장합니다.")
    remove.add_argument("--output-dir", default="reports/model_changes", help="--write-plan/--write-patch artifact 출력 디렉터리입니다.")
    remove.set_defaults(func=cmd_propose_remove)
    return parser

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
