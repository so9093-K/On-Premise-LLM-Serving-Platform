#!/usr/bin/env python3
"""Resolve EXPOSURE_MODE to a supported canonical mode.

Usage:
  python scripts/compose/resolve_exposure_mode.py [MODE]
  python scripts/compose/resolve_exposure_mode.py [MODE] --print-override-file

Returns the canonical mode name to stdout.
Exits with code 2 on unknown MODE.

This script is the single source of EXPOSURE_MODE routing logic for
compose_up.sh, preflight_compose.sh, and any other orchestration script.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

try:
    import yaml
except ModuleNotFoundError:
    raise SystemExit("Missing dependency: PyYAML. Run `python -m pip install --requirement requirements.lock`.")


def load_exposure_data(root: Path = ROOT) -> dict:
    path = root / "configs" / "exposure_profiles.yaml"
    if not path.exists():
        raise SystemExit(f"configs/exposure_profiles.yaml not found at {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def resolve(mode: str, data: dict) -> str:
    """Return the canonical mode, or exit with code 2 if mode is unknown."""
    canonical_modes: list[str] = data.get("canonical_modes", [])
    if mode in canonical_modes:
        return mode

    canonical_str = ", ".join(canonical_modes) if canonical_modes else "(none defined)"
    print(
        f"Unknown EXPOSURE_MODE={mode!r}. Allowed canonical modes: {canonical_str}.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def override_file_for(canonical_mode: str) -> str:
    """Return the compose override file path for a canonical mode, or empty string for base."""
    if canonical_mode == "private_network":
        return ""
    slug = canonical_mode.replace("_", "-")
    return f"ops/compose/overrides/exposure.{slug}.yaml"


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Resolve EXPOSURE_MODE to canonical name.")
    parser.add_argument("mode", nargs="?", default=None, help="EXPOSURE_MODE value (default: env or master_open)")
    parser.add_argument("--print-override-file", action="store_true", help="Print compose override file path instead of mode name")
    args = parser.parse_args()

    mode = args.mode or os.environ.get("EXPOSURE_MODE", "master_open")
    data = load_exposure_data()
    canonical = resolve(mode, data)

    if args.print_override_file:
        print(override_file_for(canonical))
    else:
        print(canonical)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
