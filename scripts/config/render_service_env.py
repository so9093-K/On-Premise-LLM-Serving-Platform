#!/usr/bin/env python3
"""Render a least-privilege service env file from an operator-owned env file.

The source env remains the value source of truth.  ``configs/env_contract.yaml``
owns which source keys may cross each service boundary; this script only writes
that projection and never mutates the source file.
"""
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

from ai_model_serving.settings_parts.dotenv_parser import load_strict_env_file  # noqa: E402


def _load_contract() -> dict[str, Any]:
    document = yaml.safe_load((ROOT / "configs" / "env_contract.yaml").read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("configs/env_contract.yaml must be a mapping")
    return document


def _projection(contract: dict[str, Any], target: str) -> tuple[str, dict[str, Any]]:
    projections = contract.get("service_env_projections")
    if not isinstance(projections, dict):
        raise RuntimeError("env_contract.yaml service_env_projections must be a mapping")
    matches = [
        (name, value)
        for name, value in projections.items()
        if isinstance(value, dict) and value.get("deployment_target") == target
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one service env projection for target {target!r}")
    return str(matches[0][0]), matches[0][1]


def _string_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise RuntimeError(f"{label} must be a non-empty string list")
    if len(set(value)) != len(value):
        raise RuntimeError(f"{label} must not contain duplicate keys")
    return value


def render(*, target: str, source_env: Path, output: Path) -> tuple[str, int]:
    if source_env.resolve() == output.resolve():
        raise RuntimeError("source env and rendered service env must be different files")
    values = load_strict_env_file(source_env)
    name, projection = _projection(_load_contract(), target)
    required = _string_list(
        projection.get("required_source_keys"), label=f"service_env_projections.{name}.required_source_keys"
    )
    runtime_keys = _string_list(
        projection.get("runtime_keys"), label=f"service_env_projections.{name}.runtime_keys"
    )
    unknown_required = set(required) - set(runtime_keys)
    if unknown_required:
        raise RuntimeError(
            f"service env projection {name!r} required_source_keys are not runtime_keys: "
            + ", ".join(sorted(unknown_required))
        )
    missing = [key for key in required if not values.get(key, "").strip()]
    if missing:
        raise RuntimeError(
            f"{source_env} is missing required {target} Gateway values: " + ", ".join(missing)
        )

    rendered = [f"{key}={values[key]}" for key in runtime_keys if key in values]
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(rendered))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return name, len(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a target-specific Gateway service env projection.")
    parser.add_argument("--target", required=True)
    parser.add_argument("--source-env", default=".env")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    source_env = Path(args.source_env).resolve()
    output = Path(args.output).resolve()
    try:
        name, count = render(target=args.target, source_env=source_env, output=output)
    except (OSError, RuntimeError) as exc:
        print(f"[service-env] fail: {exc}", file=sys.stderr)
        return 2
    print(f"[service-env] rendered {name}: {count} keys -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
