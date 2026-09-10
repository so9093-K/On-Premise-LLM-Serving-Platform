#!/usr/bin/env python3
"""Source configuration과 runtime API에서 정적 배포 artifact를 생성한다.

생성 대상:
  ops/prometheus/prometheus.yml
  ops/prometheus/prometheus.macos-metal.yml
  specs/schemas/model_list_response.schema.json
  specs/openapi.gateway.yaml
  specs/openapi.risk-adapter.yaml

생성 제외 대상:
  compose 파일 (full-stack.private-network.yaml):
    compose 파일에는 registry에서 파생할 수 없는 보일러플레이트가 있다
    (gateway env, prometheus secret, grafana env, healthcheck 등). PyYAML
    round-trip은 주석과 env placeholder를 유실한다.
    compose vLLM command drift는 make validate가
    validate_vllm_compose.py로 별도 검증한다.

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
sys.path.insert(0, str(ROOT))

from ai_model_serving.domain import ModelRegistry  # noqa: E402
from ai_model_serving.deployment_target import load_deployment_target  # noqa: E402
from ai_model_serving.monitoring_projection import (  # noqa: E402
    macos_metal_prometheus_config_document,
    prometheus_scrape_config_document,
)
from scripts.openapi_assets import build_generated_openapi, render_static_openapi  # noqa: E402

_GENERATED_HEADER_YAML_WITH_MONITORING = (
    "# 자동 생성 파일입니다. 직접 수정하지 마세요.\n"
    "# 소스: configs/model_catalog.yaml + configs/model_serving.yaml + configs/monitoring.yaml + configs/services.yaml\n"
    "# 명령: make render-runtime-assets\n"
)

_GENERATED_HEADER_YAML_MACOS_METAL = (
    "# 자동 생성 파일입니다. 직접 수정하지 마세요.\n"
    "# 소스: configs/monitoring.yaml + configs/services.yaml + configs/deployment_targets.yaml + configs/macos_mlx_runtime.yaml\n"
    "# 명령: make render-runtime-assets\n"
)

# ── loaders ────────────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_registry_and_monitoring(
    root: Path,
) -> tuple[ModelRegistry, dict[str, Any], dict[str, Any], dict[str, Any], str]:
    registry = ModelRegistry(
        _load_yaml(root / "configs/model_catalog.yaml"),
        _load_yaml(root / "configs/model_serving.yaml"),
    )
    monitoring = _load_yaml(root / "configs/monitoring.yaml")
    services = _load_yaml(root / "configs/services.yaml")["services"]
    macos_runtime = _load_yaml(root / "configs/macos_mlx_runtime.yaml")
    metal_target = load_deployment_target(
        root / "configs/deployment_targets.yaml", "macos-metal-static"
    )
    return registry, monitoring, services, macos_runtime, metal_target.runtime_backend


# ── renderers ──────────────────────────────────────────────────────────────────

def render_prometheus_yml(registry: ModelRegistry, monitoring: dict[str, Any], services: dict[str, Any]) -> str:
    doc = prometheus_scrape_config_document(registry=registry, monitoring=monitoring, services=services)
    body = yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return _GENERATED_HEADER_YAML_WITH_MONITORING + body


def render_macos_metal_prometheus_yml(
    monitoring: dict[str, Any],
    services: dict[str, Any],
    macos_runtime: dict[str, Any],
    runtime_backend: str,
) -> str:
    doc = macos_metal_prometheus_config_document(
        monitoring=monitoring,
        services=services,
        macos_runtime=macos_runtime,
        runtime_backend=runtime_backend,
    )
    body = yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return _GENERATED_HEADER_YAML_MACOS_METAL + body


def render_model_list_schema_json(registry: ModelRegistry) -> str:
    doc = registry.model_list_schema_document()
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


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


# ── artifact map ───────────────────────────────────────────────────────────────

def get_artifacts(
    registry: ModelRegistry,
    monitoring: dict[str, Any],
    services: dict[str, Any],
    macos_runtime: dict[str, Any],
    macos_runtime_backend: str,
    root: Path,
) -> list[tuple[Path, str]]:
    """(파일 경로, expected 내용) 목록을 반환한다."""
    openapi_docs, _ = build_generated_openapi(root)
    return [
        (root / "ops/prometheus/prometheus.yml", render_prometheus_yml(registry, monitoring, services)),
        (
            root / "ops/prometheus/prometheus.macos-metal.yml",
            render_macos_metal_prometheus_yml(
                monitoring, services, macos_runtime, macos_runtime_backend
            ),
        ),
        (
            root / "specs/schemas/model_list_response.schema.json",
            render_model_list_schema_json(registry),
        ),
        (root / "specs/openapi.gateway.yaml", render_static_openapi(openapi_docs["gateway"])),
        (
            root / "specs/openapi.risk-adapter.yaml",
            render_static_openapi(openapi_docs["risk-adapter"]),
        ),
    ]


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="프로젝트 Source of Truth에서 정적 배포 artifact를 생성/검증합니다."
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
    registry, monitoring, services, macos_runtime, macos_runtime_backend = (
        _load_registry_and_monitoring(root)
    )
    artifacts = get_artifacts(
        registry, monitoring, services, macos_runtime, macos_runtime_backend, root
    )

    if args.write:
        for path, content in artifacts:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(root)}")
        return 0

    # check 또는 dry-run 모드
    drifts: list[str] = []
    for path, expected in artifacts:
        if not compare_artifact(path, expected):
            drifts.append(str(path.relative_to(root)))
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
