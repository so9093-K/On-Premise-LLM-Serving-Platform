"""exposure/auth 계약 테스트가 공유하는 최소 헬퍼.

여기에는 실제 저장소 config를 읽는 것만 둔다. validator 분기를 찔러보기 위한
합성 profile 픽스처는 두지 않는다 -- validator는 `make validate`가 진짜
configs/exposure_profiles.yaml에 대해 실행하는 것으로 보증한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "VERSION").exists() and (parent / "configs").exists():
            return parent
    raise RuntimeError("repository root not found")


ROOT = _repo_root()

EXPOSURE_PROFILES_YAML = ROOT / "configs" / "exposure_profiles.yaml"


def load_exposure() -> dict:
    return yaml.safe_load(EXPOSURE_PROFILES_YAML.read_text(encoding="utf-8"))
