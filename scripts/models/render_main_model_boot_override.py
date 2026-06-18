#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.main_model_boot import render_boot_override  # noqa: E402


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project persisted main-model intent into a generated Compose override."
    )
    parser.add_argument(
        "--catalog", type=Path, default=ROOT / "configs/main_model_profiles.yaml"
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=ROOT / ".runtime/main-model/main-model-state.json",
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    profile_id, document = render_boot_override(
        catalog_path=args.catalog,
        state_path=args.state,
        env_path=args.env_file,
    )
    _atomic_write(args.output, document)
    print(profile_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
