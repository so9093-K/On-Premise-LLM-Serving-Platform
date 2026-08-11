#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a vLLM unified-image build setting from its canonical config."
    )
    parser.add_argument(
        "--key",
        choices=("base_image", "transformers", "huggingface_hub", "transformers_min"),
        default="transformers_min",
    )
    args = parser.parse_args()
    document = yaml.safe_load(
        (ROOT / "configs/vllm_unified_build.yaml").read_text(encoding="utf-8")
    )
    value = (
        document["base_image_default"]
        if args.key == "base_image"
        else document["compatibility_pins"][args.key]
    )
    print(str(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
