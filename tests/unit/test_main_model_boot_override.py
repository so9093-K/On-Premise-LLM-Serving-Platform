from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ai_model_serving.main_model_control import MainModelStateError
from ai_model_serving.main_model_boot import render_boot_override

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "configs/main_model_profiles.yaml"


def _env(path: Path, *, profile: str, locked: bool) -> None:
    path.write_text(
        f"MAIN_LLM_BOOT_PROFILE={profile}\n"
        f"MAIN_LLM_PROFILE_LOCKED={'true' if locked else 'false'}\n",
        encoding="utf-8",
    )


def _state(path: Path, active: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "active_profile": active}),
        encoding="utf-8",
    )


def test_persisted_profile_is_projected_to_compose_command(tmp_path):
    env = tmp_path / ".env"
    state = tmp_path / "state.json"
    _env(env, profile="gemma4-26b-a4b-fp8", locked=False)
    _state(state, "gemma4-12b-unified-fp8")

    profile, override = render_boot_override(
        catalog_path=CATALOG,
        state_path=state,
        env_path=env,
    )
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    assert profile == "gemma4-12b-unified-fp8"
    assert override["services"]["main-llm-vllm"]["command"] == catalog["profiles"][
        profile
    ]["command"]
    assert override["services"]["main-llm-vllm"]["image"] == catalog["runtime"][
        "image"
    ]


def test_locked_boot_profile_overrides_persisted_profile(tmp_path):
    env = tmp_path / ".env"
    state = tmp_path / "state.json"
    _env(env, profile="gemma4-26b-a4b-fp8", locked=True)
    _state(state, "gemma4-12b-unified-fp8")
    profile, _ = render_boot_override(
        catalog_path=CATALOG,
        state_path=state,
        env_path=env,
    )
    assert profile == "gemma4-26b-a4b-fp8"


def test_corrupt_state_fails_instead_of_falling_back(tmp_path):
    env = tmp_path / ".env"
    state = tmp_path / "state.json"
    _env(env, profile="gemma4-26b-a4b-fp8", locked=False)
    state.write_text("{broken", encoding="utf-8")
    with pytest.raises(MainModelStateError):
        render_boot_override(
            catalog_path=CATALOG,
            state_path=state,
            env_path=env,
        )
