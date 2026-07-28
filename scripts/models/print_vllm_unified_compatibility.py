#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print the vLLM unified-image compatibility value from its canonical config."
    )
    parser.add_argument(
        "--key",
        choices=("transformers", "huggingface_hub", "transformers_min"),
        default="transformers_min",
    )
    args = parser.parse_args()
    document = yaml.safe_load(
        (ROOT / "configs/recommended_images.yaml").read_text(encoding="utf-8")
    )
    pins = document["images"]["vllm"]["compatibility_pins"]
    value = pins[args.key]
    print(str(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
