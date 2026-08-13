#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.domain import ModelRegistry  # noqa: E402
from ai_model_serving.runtime_validation import render_vllm_command  # noqa: E402


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="configs/model_serving.yaml에서 vLLM 시작 명령을 렌더링합니다.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--service", default="all", help="enabled runtime service key 또는 all")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    registry = ModelRegistry(
        load_yaml(root / "configs/model_catalog.yaml"),
        load_yaml(root / "configs/model_serving.yaml"),
    )
    service_keys = {service.service_key for service in registry.iter_runtime_services()}
    if args.service != "all" and args.service not in service_keys:
        raise SystemExit(f"unknown or disabled runtime service: {args.service}")
    for service in registry.iter_runtime_services():
        if args.service not in {"all", service.service_key}:
            continue
        command = render_vllm_command(service.service_key, service.config)
        print(f"# {service.service_key} -> {service.served_model_name} on port {service.port}")
        print(" ".join(shlex.quote(part) for part in command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
