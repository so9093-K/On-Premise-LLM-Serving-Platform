#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.main_model.control import load_main_model_catalog  # noqa: E402


# 이 명령은 HF model_id/revision/tokenizer만 확인한다. Compose image를 실행하거나
# 배포 .env를 읽지 않으므로, catalog의 image env 참조는 형식만 충족하는 고정값으로 해석한다.
_CATALOG_IMAGE_FIXTURE = "registry.example.com/vllm-unified@sha256:" + "0" * 64


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the HF config/tokenizer canary for every configured main-model profile."
    )
    parser.add_argument(
        "--catalog", type=Path, default=ROOT / "configs/main_model_profiles.yaml"
    )
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args(argv)

    catalog = load_main_model_catalog(
        args.catalog,
        env={
            "VLLM_IMAGE": _CATALOG_IMAGE_FIXTURE,
            "AUDIO_VLLM_IMAGE": _CATALOG_IMAGE_FIXTURE,
        },
    )
    for profile in catalog.profiles.values():
        print(f"[hf-main-model] checking profile={profile.profile_id}", flush=True)
        command = [
            sys.executable,
            str(ROOT / "scripts/models/check_hf_model_config.py"),
            "--model",
            profile.model_id,
            "--revision",
            profile.revision,
            "--check-tokenizer",
            "--json",
        ]
        if args.local_files_only:
            command.append("--local-files-only")
        subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
