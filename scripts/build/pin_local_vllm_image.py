#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.image_refs import is_immutable_image_ref, is_local_image_id  # noqa: E402
from scripts.config.setup_env import parse_env_template, write_env  # noqa: E402

UNIFIED_IMAGE_KEYS = (
    "VLLM_IMAGE",
    "EMBEDDING_KO_VLLM_IMAGE",
    "RISK_VLLM_IMAGE",
    "AUDIO_VLLM_IMAGE",
)


def resolve_local_image_id(image_ref: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", image_ref, "--format", "{{.Id}}"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "image not found"
        raise RuntimeError(f"cannot inspect locally built image {image_ref!r}: {detail}")
    image_id = result.stdout.strip()
    if not is_local_image_id(image_id):
        raise RuntimeError(
            f"docker returned an invalid image ID for {image_ref!r}: {image_id!r}"
        )
    return image_id


def pin_matching_env_values(env_path: Path, source_ref: str, image_id: str) -> list[str]:
    if not is_local_image_id(image_id):
        raise ValueError(f"invalid local image ID: {image_id!r}")
    lines, values = parse_env_template(env_path)
    matched = [key for key in UNIFIED_IMAGE_KEYS if values.get(key) == source_ref]
    if not matched:
        raise RuntimeError(
            f"{env_path} has no unified image value matching the built image {source_ref!r}; "
            "refusing to overwrite operator-owned image references"
        )
    for key in matched:
        values[key] = image_id
    write_env(lines, values, env_path)
    return matched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pin locally built unified vLLM tags to their immutable Docker image ID."
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--image", required=True)
    args = parser.parse_args(argv)

    source_ref = args.image.strip()
    if is_immutable_image_ref(source_ref):
        print(f"[local-image-pin] already immutable: {source_ref}")
        return 0
    image_id = resolve_local_image_id(source_ref)
    matched = pin_matching_env_values(args.env_file, source_ref, image_id)
    print(f"[local-image-pin] {','.join(matched)}={image_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"[local-image-pin] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
