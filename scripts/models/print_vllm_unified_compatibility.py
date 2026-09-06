#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="vLLM unified-image의 기준 설정에서 빌드 값을 출력합니다."
    )
    parser.add_argument(
        "--key",
        choices=("target_platform", "base_image", "transformers", "huggingface_hub"),
        default="transformers",
    )
    args = parser.parse_args()
    document = yaml.safe_load(
        (ROOT / "configs/vllm_unified_build.yaml").read_text(encoding="utf-8")
    )
    if args.key == "target_platform":
        value = document["target_platform"]
    elif args.key == "base_image":
        value = document["base_image_default"]
    else:
        value = document["compatibility_pins"][args.key]
    print(str(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
