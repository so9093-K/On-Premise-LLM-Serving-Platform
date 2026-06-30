#!/usr/bin/env python3
"""Runtime asset renderer.

ModelRegistry projection에서 정적 artifact를 생성한다.

생성 대상:
  ops/prometheus/prometheus.yml
  contracts/model_contracts.yaml
  specs/schemas/model_list_response.schema.json
  harness/runtime_validation_matrix.yaml
  docs/operations/full_stack_runtime.md  (generated block만 갱신)

생성 제외 대상:
  compose 파일 (full-stack.private-network.yaml):
    compose 파일에는 registry에서 파생할 수 없는 보일러플레이트가 있다
    (gateway env, prometheus secret, grafana env, healthcheck 등). PyYAML
    round-trip은 주석과 env placeholder를 유실한다.
    대신 validate_vllm_compose.py가 compose vLLM command drift를 검증하며,
    make check-runtime-assets가 이를 호출한다.

  reports/runtime/* (runtime_targets, monitoring_projection 등):
    이 파일들은 이미 make runtime-targets, make monitoring-projection 등 별도 타겟으로
    생성된다. render_runtime_assets는 이 파일들을 소유하지 않는다.

Usage:
  python scripts/render_runtime_assets.py           # dry-run (drift 보고만, exit 0)
  python scripts/render_runtime_assets.py --check   # drift 있으면 exit 1
  python scripts/render_runtime_assets.py --write   # 모든 생성 artifact 파일에 쓰기
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.domain import ModelRegistry  # noqa: E402
from ai_model_serving.errors import ERROR_STATUS  # noqa: E402
from ai_model_serving.monitoring_projection import prometheus_scrape_config_document  # noqa: E402

_GENERATED_HEADER_YAML = (
    "# GENERATED FILE. DO NOT EDIT MANUALLY.\n"
    "# Source: configs/model_catalog.yaml + configs/model_serving.yaml\n"
    "# Command: make render-runtime-assets\n"
)
_GENERATED_HEADER_YAML_WITH_MONITORING = (
    "# GENERATED FILE. DO NOT EDIT MANUALLY.\n"
    "# Source: configs/model_catalog.yaml + configs/model_serving.yaml + configs/monitoring.yaml\n"
    "# Command: make render-runtime-assets\n"
)

RUNTIME_TARGETS_BEGIN = "<!-- BEGIN GENERATED RUNTIME TARGETS -->"
RUNTIME_TARGETS_END = "<!-- END GENERATED RUNTIME TARGETS -->"


# ── loaders ────────────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_registry_and_monitoring(root: Path) -> tuple[ModelRegistry, dict[str, Any]]:
    registry = ModelRegistry(
        _load_yaml(root / "configs/model_catalog.yaml"),
        _load_yaml(root / "configs/model_serving.yaml"),
    )
    monitoring = _load_yaml(root / "configs/monitoring.yaml")
    return registry, monitoring


# ── renderers ──────────────────────────────────────────────────────────────────

def render_prometheus_yml(registry: ModelRegistry, monitoring: dict[str, Any]) -> str:
    doc = prometheus_scrape_config_document(registry=registry, monitoring=monitoring)
    body = yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return _GENERATED_HEADER_YAML_WITH_MONITORING + body


def render_model_contracts_yaml(registry: ModelRegistry) -> str:
    doc = registry.model_contracts_document()
    body = yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return _GENERATED_HEADER_YAML + body


def render_model_list_schema_json(registry: ModelRegistry) -> str:
    doc = registry.model_list_schema_document()
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def render_runtime_validation_matrix_yaml(registry: ModelRegistry) -> str:
    doc = registry.runtime_validation_matrix_document()
    body = yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return _GENERATED_HEADER_YAML + body


def render_error_reference_md() -> str:
    """code별 의미·retryable·조치 레퍼런스. errors.py(status)+error_catalog.yaml(서술) 단일 소스.

    입력(ERROR_STATUS, error_catalog.yaml)은 항상 실제 repo ROOT에서 읽는다(registry가
    실제 configs에서 로드되는 것과 동일). 출력 경로만 get_artifacts의 root를 따른다.
    """
    catalog = (_load_yaml(ROOT / "configs/error_catalog.yaml") or {}).get("errors", {})
    by_status: dict[int, list[str]] = {}
    for code, status in ERROR_STATUS.items():
        by_status.setdefault(status, []).append(code)
    lines = [
        "<!-- GENERATED FILE. DO NOT EDIT MANUALLY. -->",
        "<!-- Source: src/ai_model_serving/errors.py (ERROR_STATUS) + configs/error_catalog.yaml -->",
        "<!-- Command: make render-runtime-assets -->",
        "",
        "# 에러 코드 레퍼런스",
        "",
        "Gateway·Risk Adapter의 모든 에러는 동일한 봉투를 따른다:",
        "",
        "```json",
        '{ "error": { "code": "...", "message": "...", "param": "...",'
        ' "retryable": false, "request_id": "req_..." } }',
        "```",
        "",
        "- `code` — 안정적 기계 판독 식별자(아래 표). HTTP status와 항상 일치한다.",
        "- `param` — 오류를 일으킨 요청 필드. 예: 잘못된 출력 스펙은 `response_format.json_schema`,"
        " 잘못된 입력 데이터 포맷은 `input_audio.format`. 필드 범위 검증 오류에만 존재한다(OpenAI 호환).",
        "- `retryable` — 재시도 권장 여부. 응답값이 권위이며 아래 표는 일반값이다.",
        "- `request_id` — 지원 문의 시 인용한다.",
        "",
        "| code | HTTP | retryable | 의미 | 권장 조치 |",
        "|---|---:|:---:|---|---|",
    ]
    for status in sorted(by_status):
        for code in sorted(by_status[status]):
            meta = catalog.get(code, {}) if isinstance(catalog, dict) else {}
            retry = "✓" if meta.get("retryable") else "✗"
            meaning = str(meta.get("meaning", "")).strip()
            action = str(meta.get("action", "")).strip()
            lines.append(f"| `{code}` | {status} | {retry} | {meaning} | {action} |")
    return "\n".join(lines) + "\n"


def render_runtime_targets_block(registry: ModelRegistry) -> str:
    targets = registry.runtime_validation_targets()
    lines = [
        RUNTIME_TARGETS_BEGIN,
        "<!-- AUTO-GENERATED by scripts/render_runtime_assets.py — do not edit manually -->",
        "",
        "| service | port | logical id | served model | capabilities |",
        "|---|---:|---|---|---|",
    ]
    for t in targets:
        caps = ", ".join(f"`{c}`" for c in t.capabilities)
        lines.append(
            f"| `{t.compose_service_name}` | {t.port} | `{t.logical_id}` | `{t.served_model_name}` | {caps} |"
        )
    lines.append("")
    lines.append(RUNTIME_TARGETS_END)
    return "\n".join(lines)


# ── comparison helpers ──────────────────────────────────────────────────────────

def _strip_yaml_comments(text: str) -> str:
    """YAML 헤더 comment (#로 시작하는 줄)를 제거한다. inline comment는 대상 아님."""
    return "\n".join(line for line in text.splitlines() if not line.startswith("#"))


def compare_artifact(path: Path, expected: str) -> bool:
    """현재 파일과 expected를 semantic하게 비교한다 (comment 차이 무시).

    YAML: yaml.safe_load로 파싱 후 Python dict 비교 (필드 순서 무시).
    JSON: json.loads로 파싱 후 비교 (whitespace 무시).
    기타: 문자열 직접 비교.
    """
    if not path.exists():
        return False
    actual = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            return json.loads(actual) == json.loads(expected)
        except Exception:
            return actual == expected
    if suffix in {".yaml", ".yml"}:
        try:
            return yaml.safe_load(_strip_yaml_comments(actual)) == yaml.safe_load(
                _strip_yaml_comments(expected)
            )
        except Exception:
            return actual == expected
    return actual == expected


def compare_doc_block(path: Path, begin_marker: str, end_marker: str, block: str) -> bool:
    """doc 파일에서 begin/end marker 사이 block이 expected와 일치하는지 확인한다."""
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    begin_idx = content.find(begin_marker)
    end_idx = content.find(end_marker)
    if begin_idx == -1 or end_idx == -1:
        return False
    actual_block = content[begin_idx : end_idx + len(end_marker)]
    return actual_block.strip() == block.strip()


def patch_doc_block(content: str, begin_marker: str, end_marker: str, new_block: str) -> str:
    """begin/end marker 사이 내용(marker 포함)을 new_block으로 교체한다.

    marker가 없으면 ValueError를 발생시킨다. 문서 구조 변경 시 조용히 넘어가면
    drift를 놓칠 수 있기 때문이다.
    """
    begin_idx = content.find(begin_marker)
    end_idx = content.find(end_marker)
    if begin_idx == -1 or end_idx == -1:
        raise ValueError(
            f"generated block marker not found: {begin_marker!r} / {end_marker!r}\n"
            "문서 구조가 변경되었거나 marker가 제거된 것 같습니다."
        )
    before = content[:begin_idx]
    after = content[end_idx + len(end_marker):]
    return before + new_block + after


# ── artifact map ───────────────────────────────────────────────────────────────

def get_artifacts(
    registry: ModelRegistry, monitoring: dict[str, Any], root: Path
) -> list[tuple[Path, str]]:
    """(파일 경로, expected 내용) 목록을 반환한다."""
    return [
        (root / "ops/prometheus/prometheus.yml", render_prometheus_yml(registry, monitoring)),
        (root / "contracts/model_contracts.yaml", render_model_contracts_yaml(registry)),
        (
            root / "specs/schemas/model_list_response.schema.json",
            render_model_list_schema_json(registry),
        ),
        (
            root / "harness/runtime_validation_matrix.yaml",
            render_runtime_validation_matrix_yaml(registry),
        ),
        (root / "docs/specs/error_reference.md", render_error_reference_md()),
    ]


def get_doc_patches(
    registry: ModelRegistry, root: Path
) -> list[tuple[Path, str, str, str]]:
    """(파일 경로, begin_marker, end_marker, block_content) 목록을 반환한다."""
    return [
        (
            root / "docs/operations/full_stack_runtime.md",
            RUNTIME_TARGETS_BEGIN,
            RUNTIME_TARGETS_END,
            render_runtime_targets_block(registry),
        ),
    ]


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="ModelRegistry projection에서 runtime artifact를 생성/검증합니다."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write", action="store_true", help="생성 산출물을 실제 파일에 씁니다."
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="현재 파일과 expected를 비교하고 drift가 있으면 exit 1합니다.",
    )
    parser.add_argument("--root", default=str(ROOT), help="프로젝트 루트 경로")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    registry, monitoring = _load_registry_and_monitoring(root)
    artifacts = get_artifacts(registry, monitoring, root)
    doc_patches = get_doc_patches(registry, root)

    if args.write:
        for path, content in artifacts:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(root)}")
        for path, begin_marker, end_marker, block in doc_patches:
            if not path.exists():
                print(f"skip (not found): {path.relative_to(root)}", file=sys.stderr)
                continue
            current = path.read_text(encoding="utf-8")
            try:
                updated = patch_doc_block(current, begin_marker, end_marker, block)
            except ValueError as exc:
                print(f"error: {path.relative_to(root)}: {exc}", file=sys.stderr)
                return 1
            path.write_text(updated, encoding="utf-8")
            print(f"patched {path.relative_to(root)}")
        return 0

    # check 또는 dry-run 모드
    drifts: list[str] = []
    for path, expected in artifacts:
        if not compare_artifact(path, expected):
            drifts.append(str(path.relative_to(root)))
    for path, begin_marker, end_marker, block in doc_patches:
        if not compare_doc_block(path, begin_marker, end_marker, block):
            drifts.append(f"{path.relative_to(root)} (generated block)")

    if drifts:
        print("Runtime asset drift detected:", file=sys.stderr)
        for d in drifts:
            print(f"  {d}", file=sys.stderr)
        print("Run: make render-runtime-assets  to update.", file=sys.stderr)
        if args.check:
            return 1
        # dry-run: drift 보고만 하고 exit 0
        return 0

    print("All runtime assets are up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
