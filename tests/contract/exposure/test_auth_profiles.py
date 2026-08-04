"""configs/auth_profiles.yaml이 auth mode 정책의 유일한 source of truth인지 검증한다.

YAML 자체의 구조적 완전성과, 코드의 AUTH_MODE_EXPECTATIONS가 이 YAML에서
도출되지 별도로 하드코딩되지 않았는지를 확인한다.
"""

from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_auth_profiles_yaml_exists() -> None:
    assert AUTH_PROFILES_YAML.exists(), "configs/auth_profiles.yaml must exist"


def test_auth_profiles_yaml_is_structurally_complete() -> None:
    """verify_auth_profiles_yaml_consistency는 drift가 아니라 YAML 완전성을 검증한다."""
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import verify_auth_profiles_yaml_consistency

    diffs = verify_auth_profiles_yaml_consistency(ROOT)
    assert diffs == [], (
        "configs/auth_profiles.yaml has structural completeness violations:\n"
        + "\n".join(diffs)
    )


def test_auth_mode_expectations_derived_from_yaml() -> None:
    """AUTH_MODE_EXPECTATIONS는 하드코딩이 아니라 configs/auth_profiles.yaml에서 도출되어야 한다."""
    sys.path.insert(0, str(ROOT / "src"))
    import importlib
    import ai_model_serving.auth_control as auth_control

    yaml_profiles = yaml.safe_load(AUTH_PROFILES_YAML.read_text(encoding="utf-8")).get("profiles", {})

    for mode, yaml_profile in yaml_profiles.items():
        if mode == "custom":
            continue
        exp = auth_control.AUTH_MODE_EXPECTATIONS.get(mode)
        assert exp is not None, f"AUTH_MODE_EXPECTATIONS is missing mode {mode!r} that exists in auth_profiles.yaml"
        for field in ("api_key_required", "admin_api_key_required", "admin_endpoints_internal_only",
                      "internal_service_auth_required", "docs_enabled"):
            if field in yaml_profile:
                yaml_val = bool(yaml_profile[field])
                exp_val = bool(exp.get(field))
                assert yaml_val == exp_val, (
                    f"AUTH_MODE_EXPECTATIONS[{mode!r}][{field!r}]={exp_val} does not match "
                    f"auth_profiles.yaml[{mode!r}][{field!r}]={yaml_val}. "
                    "AUTH_MODE_EXPECTATIONS must be derived from the YAML, not hardcoded."
                )


def test_auth_profiles_yaml_contains_internal_trusted() -> None:
    data = _load_auth()
    profiles = data.get("profiles", {})
    assert "internal_trusted" in profiles, "configs/auth_profiles.yaml must define internal_trusted profile"
    p = profiles["internal_trusted"]
    assert p.get("api_key_required") is False
    assert p.get("admin_endpoints_internal_only") is True
    assert p.get("docs_enabled") is False
    assert p.get("auth_owner") == "caller_or_network"
    assert p.get("allowed_in_production") is True


def test_local_open_profile_owns_trusted_lan_full_stack_exposure() -> None:
    data = _load_auth()
    profile = data["profiles"]["local_open"]
    assert profile["default_exposure_mode"] == "master_open"
    assert profile["default_exposure_audience"] == "private_lan"
