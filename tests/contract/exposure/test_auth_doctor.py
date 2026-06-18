from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_auth_doctor_internal_trusted_requires_evidence(monkeypatch) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import diagnose_auth

    class MockSecurity:
        auth_mode = "internal_trusted"
        api_key_required = False
        admin_api_key_required = False
        admin_endpoints_internal_only = True
        internal_service_auth_required = False
        docs_enabled = False

    class MockDocumentation:
        enabled = False

    class MockSettings:
        security = MockSecurity()
        app_env = "production"
        documentation = MockDocumentation()

    monkeypatch.setenv("INTERNAL_TRUSTED_AUTH_EVIDENCE", "")
    findings = diagnose_auth(MockSettings(), ROOT)  # type: ignore[arg-type]

    assert any(f.code == "INTERNAL_TRUSTED_EVIDENCE_MISSING" and f.level == "FAIL" for f in findings)

    monkeypatch.setenv("INTERNAL_TRUSTED_AUTH_EVIDENCE", "edge gateway authenticates callers; CHG-1234")
    findings = diagnose_auth(MockSettings(), ROOT)  # type: ignore[arg-type]
    auth_fail_findings = [
        f for f in findings
        if f.level == "FAIL"
        and f.code not in ("EXPOSURE_AUDIENCE_MISSING", "EXPOSURE_PUBLIC_AUDIENCE_WITHOUT_EXPLICIT_OPT_IN")
    ]
    assert auth_fail_findings == []
    assert any(f.code == "AUTH_DELEGATED_TO_NETWORK" for f in findings)


def test_auth_doctor_custom_requires_risk_acceptance(monkeypatch) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import diagnose_auth

    class MockSecurity:
        auth_mode = "custom"
        api_key_required = True
        admin_api_key_required = True
        admin_endpoints_internal_only = False
        internal_service_auth_required = True
        docs_enabled = False

    class MockDocumentation:
        enabled = False

    class MockSettings:
        security = MockSecurity()
        app_env = "production"
        documentation = MockDocumentation()

    monkeypatch.setenv("CUSTOM_AUTH_RISK_ACCEPTED", "false")
    monkeypatch.setenv("CUSTOM_AUTH_RISK_TICKET", "")
    findings = diagnose_auth(MockSettings(), ROOT)  # type: ignore[arg-type]
    assert any(f.code == "CUSTOM_AUTH_RISK_ACCEPTANCE_REQUIRED" and f.level == "FAIL" for f in findings)

    monkeypatch.setenv("CUSTOM_AUTH_RISK_ACCEPTED", "true")
    monkeypatch.setenv("CUSTOM_AUTH_RISK_TICKET", "CHG-1234")
    findings = diagnose_auth(MockSettings(), ROOT)  # type: ignore[arg-type]
    auth_fail_findings = [
        f for f in findings
        if f.level == "FAIL"
        and f.code not in ("EXPOSURE_AUDIENCE_MISSING", "EXPOSURE_PUBLIC_AUDIENCE_WITHOUT_EXPLICIT_OPT_IN")
    ]
    assert auth_fail_findings == []


def test_auth_doctor_rejects_local_open_without_trusted_lan_policy(
    monkeypatch,
) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import diagnose_auth

    class MockSecurity:
        auth_mode = "local_open"
        api_key_required = False
        admin_api_key_required = False
        admin_endpoints_internal_only = False
        internal_service_auth_required = False
        docs_enabled = True

    class MockDocumentation:
        enabled = True

    class MockSettings:
        security = MockSecurity()
        app_env = "production"
        documentation = MockDocumentation()

    monkeypatch.setenv("EXPOSURE_MODE", "private_network")
    monkeypatch.setenv("EXPOSURE_AUDIENCE", "")
    findings = diagnose_auth(MockSettings(), ROOT)  # type: ignore[arg-type]
    assert any(
        f.code == "LOCAL_OPEN_EXPOSURE_POLICY_MISMATCH" and f.level == "FAIL"
        for f in findings
    )


def test_auth_doctor_allows_non_local_local_open_on_trusted_lan(
    monkeypatch,
) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import diagnose_auth

    class MockSecurity:
        auth_mode = "local_open"
        api_key_required = False
        admin_api_key_required = False
        admin_endpoints_internal_only = False
        internal_service_auth_required = False
        docs_enabled = True

    class MockDocumentation:
        enabled = True

    class MockSettings:
        security = MockSecurity()
        app_env = "production"
        documentation = MockDocumentation()

    monkeypatch.setenv("EXPOSURE_MODE", "master_open")
    monkeypatch.setenv("EXPOSURE_AUDIENCE", "private_lan")
    findings = diagnose_auth(MockSettings(), ROOT)  # type: ignore[arg-type]
    assert not [finding for finding in findings if finding.level == "FAIL"]
    assert any(f.code == "AUTH_DELEGATED_TO_NETWORK" for f in findings)


def test_exposure_audience_allowed_values_declared_in_yaml() -> None:
    """exposure_profiles.yaml must declare exposure_audience.allowed_values."""
    data = _load_exposure()
    allowed = data.get("exposure_audience", {}).get("allowed_values", [])
    assert isinstance(allowed, list) and len(allowed) >= 1, (
        "configs/exposure_profiles.yaml must declare exposure_audience.allowed_values "
        "with at least one value — this is the source-of-truth for EXPOSURE_AUDIENCE validation"
    )
    assert "local_only" in allowed
    assert "public" in allowed


def test_auth_doctor_rejects_invalid_exposure_audience(monkeypatch) -> None:
    """auth-doctor must FAIL for EXPOSURE_AUDIENCE with an arbitrary invalid value."""
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import diagnose_auth

    monkeypatch.setenv("EXPOSURE_MODE", "master_open")
    monkeypatch.setenv("EXPOSURE_AUDIENCE", "banana")

    findings = diagnose_auth(_make_local_settings(), ROOT)  # type: ignore[arg-type]
    codes = [f.code for f in findings if f.level == "FAIL"]
    assert "EXPOSURE_AUDIENCE_INVALID_VALUE" in codes, (
        "auth-doctor must FAIL with EXPOSURE_AUDIENCE_INVALID_VALUE for "
        "EXPOSURE_MODE=master_open + EXPOSURE_AUDIENCE=banana; "
        f"actual FAIL codes: {codes}"
    )


def test_auth_doctor_rejects_local_only_with_open_bind(monkeypatch) -> None:
    """auth-doctor must FAIL when local_only is declared but services bind to 0.0.0.0."""
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import diagnose_auth

    services = _load_services()
    monkeypatch.setenv("EXPOSURE_MODE", "master_open")
    monkeypatch.setenv("EXPOSURE_AUDIENCE", "local_only")
    for svc in services.values():
        bind_env = svc.get("host_env_bind", "")
        if bind_env:
            monkeypatch.setenv(bind_env, "0.0.0.0")

    findings = diagnose_auth(_make_local_settings(), ROOT)  # type: ignore[arg-type]
    codes = [f.code for f in findings if f.level == "FAIL"]
    assert "EXPOSURE_LOCAL_ONLY_BIND_MISMATCH" in codes, (
        "auth-doctor must FAIL with EXPOSURE_LOCAL_ONLY_BIND_MISMATCH for "
        "EXPOSURE_MODE=master_open + EXPOSURE_AUDIENCE=local_only with default 0.0.0.0 binds; "
        f"actual FAIL codes: {codes}"
    )


def test_auth_doctor_passes_local_only_with_loopback_bind(monkeypatch) -> None:
    """auth-doctor must NOT produce EXPOSURE_LOCAL_ONLY_BIND_MISMATCH when all binds are 127.0.0.1."""
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import diagnose_auth

    services = _load_services()

    monkeypatch.setenv("EXPOSURE_MODE", "master_open")
    monkeypatch.setenv("EXPOSURE_AUDIENCE", "local_only")
    # Set all host_env_bind vars to loopback
    for svc in services.values():
        bind_env = svc.get("host_env_bind", "")
        if bind_env:
            monkeypatch.setenv(bind_env, "127.0.0.1")

    findings = diagnose_auth(_make_local_settings(), ROOT)  # type: ignore[arg-type]
    codes = [f.code for f in findings if f.level == "FAIL"]
    assert "EXPOSURE_LOCAL_ONLY_BIND_MISMATCH" not in codes, (
        "auth-doctor must NOT produce EXPOSURE_LOCAL_ONLY_BIND_MISMATCH when all bind addrs are 127.0.0.1; "
        f"actual FAIL codes: {codes}"
    )


def test_auth_doctor_passes_private_lan_with_open_bind(monkeypatch) -> None:
    """auth-doctor must NOT produce bind mismatch for EXPOSURE_AUDIENCE=private_lan + 0.0.0.0."""
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import diagnose_auth

    monkeypatch.setenv("EXPOSURE_MODE", "master_open")
    monkeypatch.setenv("EXPOSURE_AUDIENCE", "private_lan")
    # 0.0.0.0 bind is intentional for private_lan — gateway/VPN controls access

    findings = diagnose_auth(_make_local_settings(), ROOT)  # type: ignore[arg-type]
    codes = [f.code for f in findings if f.level == "FAIL"]
    assert "EXPOSURE_LOCAL_ONLY_BIND_MISMATCH" not in codes, (
        "auth-doctor must not produce bind mismatch for EXPOSURE_AUDIENCE=private_lan; "
        f"actual FAIL codes: {codes}"
    )
    assert "EXPOSURE_AUDIENCE_INVALID_VALUE" not in codes, (
        "private_lan must be a valid EXPOSURE_AUDIENCE value; "
        f"actual FAIL codes: {codes}"
    )


def test_auth_doctor_reads_exposure_audience_from_explicit_env_file(monkeypatch, tmp_path) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import diagnose_auth
    from ai_model_serving.settings import load_settings

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "APP_ENV=local",
                "AUTH_MODE=local_open",
                "API_KEY_REQUIRED=false",
                "ADMIN_API_KEY_REQUIRED=false",
                "ADMIN_ENDPOINTS_INTERNAL_ONLY=false",
                "INTERNAL_SERVICE_AUTH_REQUIRED=false",
                "FASTAPI_DOCS_ENABLED=true",
                "EXPOSURE_MODE=master_open",
                "EXPOSURE_AUDIENCE=banana",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EXPOSURE_MODE", raising=False)
    monkeypatch.delenv("EXPOSURE_AUDIENCE", raising=False)

    settings = load_settings(env_file=env_file)
    findings = diagnose_auth(settings, ROOT)

    codes = [f.code for f in findings if f.level == "FAIL"]
    assert "EXPOSURE_AUDIENCE_INVALID_VALUE" in codes


def test_auth_doctor_reads_local_only_binds_from_explicit_env_file(monkeypatch, tmp_path) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import diagnose_auth
    from ai_model_serving.settings import load_settings

    services = _load_services()
    bind_lines = [
        f"{svc.get('host_env_bind')}=127.0.0.1"
        for svc in services.values()
        if svc.get("host_env_bind")
    ]
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "APP_ENV=local",
                "AUTH_MODE=local_open",
                "API_KEY_REQUIRED=false",
                "ADMIN_API_KEY_REQUIRED=false",
                "ADMIN_ENDPOINTS_INTERNAL_ONLY=false",
                "INTERNAL_SERVICE_AUTH_REQUIRED=false",
                "FASTAPI_DOCS_ENABLED=true",
                "EXPOSURE_MODE=master_open",
                "EXPOSURE_AUDIENCE=local_only",
                *bind_lines,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for key in ("EXPOSURE_MODE", "EXPOSURE_AUDIENCE"):
        monkeypatch.delenv(key, raising=False)
    for svc in services.values():
        bind_env = svc.get("host_env_bind", "")
        if bind_env:
            monkeypatch.delenv(bind_env, raising=False)

    settings = load_settings(env_file=env_file)
    findings = diagnose_auth(settings, ROOT)

    codes = [f.code for f in findings if f.level == "FAIL"]
    assert "EXPOSURE_LOCAL_ONLY_BIND_MISMATCH" not in codes
