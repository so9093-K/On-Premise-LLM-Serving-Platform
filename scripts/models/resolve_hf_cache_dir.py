#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.main_model.boot import (  # noqa: E402
    read_env_values,
    resolve_compose_relative_path,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve HF_CACHE_DIR with Docker Compose file-relative semantics."
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=ROOT / "ops/compose/full-stack.private-network.yaml",
    )
    args = parser.parse_args(argv)
    env = read_env_values(args.env_file)
    print(
        resolve_compose_relative_path(
            env.get("HF_CACHE_DIR", "./model_cache/huggingface"),
            args.compose_file,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
