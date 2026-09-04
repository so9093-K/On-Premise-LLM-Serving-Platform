from pathlib import Path

from ai_model_serving.serving_profile import load_main_serving_catalog


def test_main_serving_projection_excludes_runtime_control_fields() -> None:
    root = Path(__file__).resolve().parents[2]
    catalog = load_main_serving_catalog(root / "configs" / "main_model_profiles.yaml")
    profile = catalog.profiles[catalog.default_profile]

    assert profile.served_model_name == catalog.public_model == "local-main"
    assert profile.gateway_policy["request_limits"]["max_model_len"] > 0
    assert not hasattr(profile, "image")
    assert not hasattr(profile, "command")
    assert not hasattr(profile, "vram_fraction")
