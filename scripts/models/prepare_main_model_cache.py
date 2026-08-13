#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.main_model.control import load_main_model_catalog  # noqa: E402
from ai_model_serving.main_model.boot import (  # noqa: E402
    read_env_values,
    resolve_compose_relative_path,
)
from ai_model_serving.main_model.cache import prepare_model_snapshot  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare an allowlisted main-model profile without changing the active runtime."
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--catalog", type=Path, default=ROOT / "configs/main_model_profiles.yaml"
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=ROOT / "ops/compose/full-stack.private-network.yaml",
    )
    args = parser.parse_args(argv)

    # 이 명령은 cache만 준비하며 runtime을 만들지 않는다. image digest 해석은
    # Compose/sidecar의 runtime 경계에서만 수행한다.
    catalog = load_main_model_catalog(args.catalog, resolve_runtime_images=False)
    try:
        profile = catalog.profiles[args.profile]
    except KeyError as exc:
        raise SystemExit(f"unknown main-model profile: {args.profile}") from exc
    env = read_env_values(args.env_file)
    cache_dir = resolve_compose_relative_path(
        env.get("HF_CACHE_DIR", "./model_cache/huggingface"),
        args.compose_file,
    )
    token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or env.get("HF_TOKEN")
        or env.get("HUGGING_FACE_HUB_TOKEN")
    )
    prepared = prepare_model_snapshot(
        model_id=profile.model_id,
        revision=profile.revision,
        cache_dir=cache_dir,
        token=token,
    )
    print(
        f"prepared profile={profile.profile_id} model={prepared.model_id} "
        f"revision={prepared.revision} cache={cache_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
