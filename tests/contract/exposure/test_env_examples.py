from __future__ import annotations

import pytest

from .helpers import *  # noqa: F401,F403

def _env_example_key_group_cases() -> list[dict[str, str]]:
    manifest = yaml.safe_load((ROOT / "tests/contract/manifests/env_examples.yaml").read_text(encoding="utf-8"))
    return list(manifest["env_example_required_key_groups"])


@pytest.mark.parametrize("case", _env_example_key_group_cases(), ids=lambda case: case["id"])
def test_env_example_contains_required_key_group(case: dict[str, str]) -> None:
    keys = _env_required_model_runtime_keys()
    required_keys = [key for key in keys if key.startswith(case["key_prefix"])]
    missing = _check_env_file(ROOT / case["path"], required_keys)
    assert missing == [], f"{case['path']} missing {case['key_prefix']} runtime keys: {missing}"


def test_env_compose_example_contains_exposure_mode() -> None:
    text = (ROOT / ".env.compose.example").read_text(encoding="utf-8")
    assert "EXPOSURE_MODE" in text, ".env.compose.example must define EXPOSURE_MODE"


def test_env_compose_example_contains_exposure_audience() -> None:
    text = (ROOT / ".env.compose.example").read_text(encoding="utf-8")
    assert "EXPOSURE_AUDIENCE" in text, (
        ".env.compose.example must define EXPOSURE_AUDIENCE. "
        "This is required for diagnostic_full_stack (master_open) profiles."
    )


def test_env_examples_contain_bind_addr_keys() -> None:
    bind_keys = _env_required_exposure_bind_keys()
    for example in (".env.example", ".env.compose.example"):
        missing = _check_env_file(ROOT / example, bind_keys)
        assert missing == [], f"{example} missing bind addr keys derived from exposure sources: {missing}"
