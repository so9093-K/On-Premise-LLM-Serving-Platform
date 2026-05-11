from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SAFE_DETAIL_KEYS = {
    "ids",
    "expected_model",
    "status",
    "forbidden_fields",
    "model",
    "choices",
    "dimension",
    "present",
    "missing",
    "up_jobs",
    "total_mib",
    "used_mib",
    "reserve_gib",
    "minimum_reserve_gib",
    "iterations",
    "errors",
    "max_latency_ms",
    "avg_latency_ms",
    "targets",
    "model_ids",
    "capabilities",
    "runtime_targets",
    "compose_service_regex",
    "scrape_jobs",
    "model_labels",
    "runtime_service_labels",
    "total_gpu_memory_utilization",
    "recommended_start",
    "avoid_above",
    "profile",
    "fixed_constraints",
    "skipped",
}
SENSITIVE_DETAIL_KEYS = {
    "authorization",
    "api_key",
    "admin_api_key",
    "internal_service_token",
    "token",
    "secret",
    "password",
    "prompt",
    "messages",
    "input",
    "output",
    "completion",
    "embedding",
    "raw_prompt",
    "user_text",
    "model_output",
}


def _normalise_key(value: str) -> str:
    return value.lower().replace("-", "_")


def _safe_details(details: Any) -> dict[str, Any]:
    if not isinstance(details, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, value in details.items():
        lowered = _normalise_key(str(key))
        if any(token in lowered for token in SENSITIVE_DETAIL_KEYS):
            continue
        if lowered in SAFE_DETAIL_KEYS:
            safe[str(key)] = value
    return safe


def _runtime_report_mode(path: Path) -> str:
    """Read a runtime-validation report mode without exposing report payloads."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    return str(payload.get("mode", "unknown"))


def latest_runtime_validation_report(
    root: Path,
    output_dir: str = "reports/runtime",
    *,
    prefer_live: bool = True,
) -> Path | None:
    """Return the newest runtime_validation_*.json report.

    By default this prefers the newest non-``config-only`` report when one is
    present. This prevents a static release check from creating a newer
    config-only report and accidentally hiding an older GPU/live validation
    artifact in the live-evidence bundle.
    """
    report_dir = root / output_dir
    candidates = sorted(report_dir.glob("runtime_validation_*.json"), key=lambda path: path.stat().st_mtime)
    if not candidates:
        return None
    if prefer_live:
        for candidate in reversed(candidates):
            if _runtime_report_mode(candidate) != "config-only":
                return candidate
    return candidates[-1]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sanitised_runtime_results(runtime_report: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in runtime_report.get("results", []):
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "category": str(item.get("category", "")),
                "name": str(item.get("name", "")),
                "status": str(item.get("status", "")),
                "latency_ms": item.get("latency_ms"),
                "detail": str(item.get("detail", ""))[:400],
                "details": _safe_details(item.get("details", {})),
            }
        )
    return results


def _category_summary(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for item in results:
        category = item.get("category") or "unknown"
        status = "passed" if item.get("status") == "pass" else "failed"
        summary.setdefault(category, {"passed": 0, "failed": 0})[status] += 1
    return summary


def _missing_runtime_categories(results: list[dict[str, Any]]) -> list[str]:
    required = {
        "gateway-runtime",
        "risk-adapter-runtime",
        "vllm-runtime",
        "monitoring-scrape",
        "gpu-capacity",
    }
    seen = {item.get("category") for item in results if item.get("category")}
    return sorted(required - seen)


def live_evidence_bundle_document(
    *,
    operator_status: dict[str, Any],
    runtime_report: dict[str, Any] | None,
    runtime_report_path: str | None,
    version: str,
) -> dict[str, Any]:
    """Combine static operator projections with a sanitised runtime validation report."""
    runtime_results = _sanitised_runtime_results(runtime_report or {})
    runtime_summary = (runtime_report or {}).get("summary", {}) if runtime_report else {}
    runtime_mode = str((runtime_report or {}).get("mode", "missing"))
    missing_categories = _missing_runtime_categories(runtime_results) if runtime_results else [
        "gateway-runtime",
        "risk-adapter-runtime",
        "vllm-runtime",
        "monitoring-scrape",
        "gpu-capacity",
    ]
    failed = int(runtime_summary.get("failed", len([item for item in runtime_results if item.get("status") != "pass"])))
    if not runtime_report:
        evidence_status = "missing_runtime_evidence"
    elif runtime_mode == "config-only":
        evidence_status = "static_only"
    elif failed:
        evidence_status = "live_failed"
    elif missing_categories:
        evidence_status = "live_partial"
    else:
        evidence_status = "live_validated"

    runtime_targets = operator_status.get("runtime_targets", [])
    return {
        "version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_of_truth": [
            "reports/runtime/operator_status_bundle.json",
            runtime_report_path or "reports/runtime/runtime_validation_*.json",
        ],
        "privacy_contract": {
            "raw_prompt_included": False,
            "user_text_included": False,
            "model_output_included": False,
            "authorization_header_included": False,
            "runtime_details_are_sanitised": True,
        },
        "evidence_status": evidence_status,
        "runtime_report": {
            "path": runtime_report_path,
            "mode": runtime_mode,
            "started_at": (runtime_report or {}).get("started_at"),
            "finished_at": (runtime_report or {}).get("finished_at"),
            "summary": runtime_summary or {"passed": 0, "failed": 0},
            "missing_live_categories": missing_categories,
        },
        "static_operator_bundle": {
            "runtime_target_count": len(runtime_targets),
            "model_ids": [target.get("logical_id", "") for target in runtime_targets],
            "runtime_services": [target.get("service_key", "") for target in runtime_targets],
            "compose_service_regex": operator_status.get("compose_service_regex", ""),
            "monitoring_scrape_jobs": operator_status.get("monitoring_projection", {}).get("prometheus_scrape_jobs", []),
            "readiness_statuses": operator_status.get("readiness_vocabulary", {}).get("statuses", []),
        },
        "runtime_category_summary": _category_summary(runtime_results),
        "runtime_results": runtime_results,
        "operator_commands": {
            "generate_static_bundle": "make operator-status",
            "run_live_validation": "make runtime-validate",
            "generate_live_evidence": "make live-evidence",
            "run_release_gate": "make release-check",
        },
    }


def live_evidence_bundle_markdown(document: dict[str, Any]) -> str:
    runtime = document.get("runtime_report", {})
    static_bundle = document.get("static_operator_bundle", {})
    lines = [
        "# 라이브 증빙 번들",
        "",
        f"버전: `{document.get('version', '')}`",
        f"증빙 상태: `{document.get('evidence_status', '')}`",
        "",
        "이 리포트는 정적 운영 상태 번들과 sanitised runtime validation report를 결합한다. 원문 프롬프트, 사용자 텍스트, 모델 출력, Authorization 헤더, secret 값은 포함하지 않는다.",
        "",
        "## 런타임 증빙",
        "",
        f"Runtime report: `{runtime.get('path') or 'not found'}`",
        f"모드: `{runtime.get('mode', '')}`",
        f"통과: `{runtime.get('summary', {}).get('passed', 0)}` / 실패: `{runtime.get('summary', {}).get('failed', 0)}`",
        f"누락된 live category: `{', '.join(runtime.get('missing_live_categories', [])) or '-'}`",
        "",
        "## 정적 운영 projection",
        "",
        f"런타임 대상: `{static_bundle.get('runtime_target_count', 0)}`",
        f"모델: `{', '.join(static_bundle.get('model_ids', []))}`",
        f"런타임 서비스: `{', '.join(static_bundle.get('runtime_services', []))}`",
        f"Compose 서비스 정규식: `{static_bundle.get('compose_service_regex', '')}`",
        f"Prometheus scrape job: `{', '.join(static_bundle.get('monitoring_scrape_jobs', []))}`",
        "",
        "## Runtime category 요약",
        "",
        "| Category | 통과 | 실패 |",
        "|---|---:|---:|",
    ]
    for category, counts in sorted(document.get("runtime_category_summary", {}).items()):
        lines.append(f"| {category} | {counts.get('passed', 0)} | {counts.get('failed', 0)} |")
    commands = document.get("operator_commands", {})
    lines.extend([
        "",
        "## 운영 명령",
        "",
        f"- 정적 번들: `{commands.get('generate_static_bundle', '')}`",
        f"- 라이브 runtime 검증: `{commands.get('run_live_validation', '')}`",
        f"- 라이브 증빙 번들: `{commands.get('generate_live_evidence', '')}`",
        f"- 릴리스 gate: `{commands.get('run_release_gate', '')}`",
    ])
    return "\n".join(lines) + "\n"


def write_live_evidence_bundle(document: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "live_evidence_bundle.json"
    md_path = output_dir / "live_evidence_bundle.md"
    json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(live_evidence_bundle_markdown(document), encoding="utf-8")
    return json_path, md_path
