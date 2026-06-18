from __future__ import annotations

import argparse
import json
from pathlib import Path

from .main_model_control import load_main_model_catalog
from .model_cache import prepare_model_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare one allowlisted main-model profile in the Hugging Face cache."
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("/app/configs/main_model_profiles.yaml"),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("/root/.cache/huggingface"),
    )
    args = parser.parse_args(argv)

    catalog = load_main_model_catalog(args.catalog)
    try:
        profile = catalog.profiles[args.profile]
    except KeyError as exc:
        raise SystemExit(f"unknown main-model profile: {args.profile}") from exc
    prepared = prepare_model_snapshot(
        model_id=profile.model_id,
        revision=profile.revision,
        cache_dir=args.cache_dir,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "profile": profile.profile_id,
                "model_id": prepared.model_id,
                "revision": prepared.revision,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
