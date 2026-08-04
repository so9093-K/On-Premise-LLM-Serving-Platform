"""configs/auth_profiles.yaml이 auth mode 정책의 유일한 source of truth인지 검증한다.

YAML 자체의 구조적 완전성과, 코드의 AUTH_MODE_EXPECTATIONS가 이 YAML에서
도출되지 별도로 하드코딩되지 않았는지를 확인한다.
"""

from __future__ import annotations

from .helpers import *  # noqa: F401,F403

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
