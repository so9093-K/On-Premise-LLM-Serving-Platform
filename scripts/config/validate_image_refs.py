#!/usr/bin/env python3
"""Container image authority와 remote env immutable 계약을 검증한다."""

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
_ALLOWED_REFERENCE_POLICIES = {"local_build", "immutable_upstream"}


def _image_specs(path: Path = IMAGE_CONFIG) -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    images = raw.get("images") if isinstance(raw, dict) else None
    if not isinstance(images, dict):
        raise RuntimeError(f"{path}: images mapping is required")
    return images


def _validated_entries(
    specs: dict[str, dict[str, Any]],
) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    entries: list[tuple[str, str, str, str]] = []
    errors: list[str] = []
    env_keys: set[str] = set()

    for name, spec in specs.items():
        if not isinstance(spec, dict):
            errors.append(f"recommended image {name!r} must be a mapping")
            continue

        env_key = str(spec.get("env_key", "")).strip()
        reference_policy = str(spec.get("reference_policy", "")).strip()
        default = str(spec.get("default", "")).strip()

        if not env_key:
            errors.append(f"recommended image {name!r} must define env_key")
        elif env_key in env_keys:
            errors.append(f"recommended image env_key must be unique: {env_key}")
        else:
            env_keys.add(env_key)

        if not default:
            errors.append(f"recommended image {name!r} must define default")

        if reference_policy not in _ALLOWED_REFERENCE_POLICIES:
            errors.append(
                f"recommended image {name!r} reference_policy must be one of "
                f"{', '.join(sorted(_ALLOWED_REFERENCE_POLICIES))}"
            )
        elif (
            reference_policy == "immutable_upstream"
            and default
            and not is_registry_digest_image_ref(default)
        ):
            errors.append(
                f"configs/recommended_images.yaml images.{name}.default for {env_key or '<missing env_key>'} "
                "must be an immutable registry digest"
            )

        if env_key and default and reference_policy in _ALLOWED_REFERENCE_POLICIES:
            entries.append((str(name), env_key, reference_policy, default))

    return entries, errors


def remote_env_errors(
    values: dict[str, str],
    *,
    specs: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    specs = specs or _image_specs()
    entries, errors = _validated_entries(specs)
    for name, env_key, reference_policy, _default in entries:
        if reference_policy != "immutable_upstream":
            continue
        value = values.get(env_key, "").strip()
        if not is_registry_digest_image_ref(value):
            errors.append(
                f"{env_key} ({name}) must be an immutable registry digest "
                "(name@sha256:<64 lowercase hex>)"
            )
    return errors


def repository_contract_errors() -> list[str]:
    specs = _image_specs()
    entries, errors = _validated_entries(specs)
    immutable_entries = [
        entry for entry in entries if entry[2] == "immutable_upstream"
    ]
    if not immutable_entries:
        errors.append(
            "recommended_images.yaml declares no immutable_upstream images"
        )

    example = load_strict_env_file(COMPOSE_EXAMPLE)
    for name, env_key, _reference_policy, default in entries:
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
        description="Validate repository image authority and remote immutable refs."
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
