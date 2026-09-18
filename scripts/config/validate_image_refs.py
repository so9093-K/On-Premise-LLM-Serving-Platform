#!/usr/bin/env python3
"""Third-party Compose image의 immutable default와 remote env 계약을 검증한다."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ai_model_serving.image_refs import is_registry_digest_image_ref  # noqa: E402
from ai_model_serving.settings_parts.dotenv_parser import load_strict_env_file  # noqa: E402

IMAGE_CONFIG = ROOT / "configs" / "recommended_images.yaml"
COMPOSE_EXAMPLE = ROOT / ".env.compose.example"


def _image_specs(path: Path = IMAGE_CONFIG) -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    images = raw.get("images") if isinstance(raw, dict) else None
    if not isinstance(images, dict):
        raise RuntimeError(f"{path}: images mapping is required")
    return images


def _remote_immutable_specs(
    specs: dict[str, dict[str, Any]],
) -> list[tuple[str, str, str]]:
    selected: list[tuple[str, str, str]] = []
    for name, spec in specs.items():
        if not isinstance(spec, dict) or spec.get("remote_immutable") is not True:
            continue
        default = str(spec.get("default", "")).strip()
        selected.append((str(name), f"{str(name).upper()}_IMAGE", default))
    return selected


def remote_env_errors(
    values: dict[str, str],
    *,
    specs: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    specs = specs or _image_specs()
    errors: list[str] = []
    for name, env_key, _default in _remote_immutable_specs(specs):
        value = values.get(env_key, "").strip()
        if not is_registry_digest_image_ref(value):
            errors.append(
                f"{env_key} ({name}) must be an immutable registry digest "
                "(name@sha256:<64 lowercase hex>)"
            )
    return errors


def repository_contract_errors() -> list[str]:
    specs = _image_specs()
    errors: list[str] = []
    selected = _remote_immutable_specs(specs)
    if not selected:
        errors.append("recommended_images.yaml declares no remote_immutable images")
        return errors

    for name, env_key, default in selected:
        if not is_registry_digest_image_ref(default):
            errors.append(
                f"configs/recommended_images.yaml images.{name}.default for {env_key} "
                "must be an immutable registry digest"
            )

    example = load_strict_env_file(COMPOSE_EXAMPLE)
    errors.extend(remote_env_errors(example, specs=specs))
    for name, env_key, default in selected:
        if example.get(env_key, "").strip() != default:
            errors.append(
                f".env.compose.example {env_key} must project "
                f"configs/recommended_images.yaml images.{name}.default"
            )
    return errors


def validate_repository_image_refs() -> None:
    errors = repository_contract_errors()
    if errors:
        raise SystemExit("\n".join(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate immutable third-party Compose image refs."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Validate a deployment env instead of repository defaults/projection.",
    )
    args = parser.parse_args(argv)

    try:
        if args.env_file is None:
            errors = repository_contract_errors()
        else:
            values = load_strict_env_file(args.env_file)
            errors = remote_env_errors(values)
    except (OSError, RuntimeError, yaml.YAMLError) as exc:
        print(f"[image-ref] ERROR: {exc}", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(f"[image-ref] ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
