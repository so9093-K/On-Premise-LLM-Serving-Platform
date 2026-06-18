#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    document = yaml.safe_load(
        (ROOT / "configs/recommended_images.yaml").read_text(encoding="utf-8")
    )
    value = document["images"]["risk_vllm"]["compatibility_pins"][
        "transformers_min"
    ]
    print(str(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
