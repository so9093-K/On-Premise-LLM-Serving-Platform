"""Contract tests for auth/exposure profile source-of-truth consistency.

Design principle: tests verify structural invariants and policy constraints,
not specific past-mistake mode names. If canonical mode names change,
tests continue to verify the invariants against whatever canonical_modes declares.

Verifies:
- configs/exposure_profiles.yaml structural invariants via the validator integration
- configs/auth_profiles.yaml completeness (via verify_auth_profiles_yaml_consistency)
- auth_control.AUTH_MODE_EXPECTATIONS is derived from YAML (not a separate hardcoded dict)
- env examples contain all env_contract required keys
- bootstrap.sh applies named auth modes without private_network-specific skipping
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
AUTH_PROFILES_YAML = ROOT / "configs" / "auth_profiles.yaml"
EXPOSURE_PROFILES_YAML = ROOT / "configs" / "exposure_profiles.yaml"
SERVICES_YAML = ROOT / "configs" / "services.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_exposure() -> dict:
    return yaml.safe_load(EXPOSURE_PROFILES_YAML.read_text(encoding="utf-8"))


def _load_auth() -> dict:
    return yaml.safe_load(AUTH_PROFILES_YAML.read_text(encoding="utf-8"))


def _load_services() -> dict:
    return yaml.safe_load(SERVICES_YAML.read_text(encoding="utf-8")).get("services", {})


def _canonical_modes(data: dict) -> list[str]:
    return data.get("canonical_modes", [])


# ---------------------------------------------------------------------------
# A. resolve_exposure_mode.py decision function
# ---------------------------------------------------------------------------

def test_resolve_exposure_mode_returns_canonical_for_canonical() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.compose.resolve_exposure_mode import load_exposure_data, resolve
    data = load_exposure_data(ROOT)
    for mode in _canonical_modes(data):
        canonical = resolve(mode, data)
        assert canonical == mode, f"resolve({mode!r}) should return canonical mode, got {canonical!r}"


def test_resolve_exposure_mode_fails_on_unknown_mode() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.compose.resolve_exposure_mode import load_exposure_data, resolve
    import pytest
    data = load_exposure_data(ROOT)
    with pytest.raises(SystemExit) as exc_info:
        resolve("unsupported_mode", data)
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# B. validate_exposure_profiles.py structural validator
# ---------------------------------------------------------------------------

def test_validate_exposure_profiles_strict_passes_on_current_yaml() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_exposure_profiles import load, validate
    data = load(EXPOSURE_PROFILES_YAML)
    violations = validate(data, strict=True)
    assert violations == [], (
        "validate_exposure_profiles (strict) found violations:\n" + "\n".join(violations)
    )


def _profile_diagnostics(*, diagnostic: bool) -> dict[str, bool]:
    return {
        "gateway_bypass_possible": diagnostic,
        "direct_model_runtime_access": diagnostic,
        "direct_risk_adapter_access": diagnostic,
        "direct_operations_endpoints": diagnostic,
        "requires_exposure_audience": diagnostic,
    }


def _validator_service(*categories: str) -> dict:
    return {
        "compose_service": "fixture-service",
        "container_port": 9000,
        "host_env_port": "FIXTURE_PORT",
        "default_host_port": 9000,
        "host_env_bind": "FIXTURE_BIND_ADDR",
        "default_bind": "127.0.0.1",
        "categories": list(categories),
    }


def _category_validator_fixture() -> tuple[dict, dict]:
    services = {
        "entry": _validator_service("gateway"),
        "runtime_a": _validator_service("model_runtime"),
        "runtime_b": _validator_service("model_runtime"),
        "risk": _validator_service("risk_adapter"),
        "ops": _validator_service("operations_endpoint"),
        "view": _validator_service("visualization"),
    }
    data = {
        "canonical_modes": ["private", "diagnostic"],
        "profiles": {
            "private": {
                "class": "default_private",
                "description": "fixture private",
                "host_published": ["entry", "view"],
                "diagnostics": _profile_diagnostics(diagnostic=False),
            },
            "diagnostic": {
                "class": "diagnostic_full_stack",
                "description": "fixture diagnostic",
                "host_published": list(services),
                "diagnostics": _profile_diagnostics(diagnostic=True),
            },
        },
    }
    return data, services


def test_exposure_validator_requires_service_categories() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_exposure_profiles import validate

    data, services = _category_validator_fixture()
    services["runtime_a"].pop("categories")

    violations = validate(data, services=services)

    assert any(".categories" in violation for violation in violations)


def test_exposure_validator_rejects_default_private_operations_category() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_exposure_profiles import validate

    data, services = _category_validator_fixture()
    data["profiles"]["private"]["host_published"].append("ops")

    violations = validate(data, services=services)

    assert any(
        "default_private profile must not host-publish operations_endpoint services" in violation
        for violation in violations
    )


def test_exposure_validator_requires_all_model_runtime_services_for_diagnostic_profile() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_exposure_profiles import validate

    data, services = _category_validator_fixture()
    data["profiles"]["diagnostic"]["host_published"].remove("runtime_b")

    violations = validate(data, services=services)

    assert any(
        "diagnostic_full_stack profile is missing model_runtime services" in violation
        for violation in violations
    )


def test_exposure_validator_requires_diagnostic_visualization_category() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_exposure_profiles import validate

    data, services = _category_validator_fixture()
    data["profiles"]["diagnostic"]["host_published"].remove("view")

    violations = validate(data, services=services)

    assert any(
        "diagnostic_full_stack profile must host-publish at least one visualization service" in violation
        for violation in violations
    )


# ---------------------------------------------------------------------------
# C. configs/auth_profiles.yaml — YAML is actual source of truth
# ---------------------------------------------------------------------------

def test_auth_profiles_yaml_exists() -> None:
    assert AUTH_PROFILES_YAML.exists(), "configs/auth_profiles.yaml must exist"


def test_auth_profiles_yaml_is_structurally_complete() -> None:
    """verify_auth_profiles_yaml_consistency validates YAML completeness, not drift."""
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import verify_auth_profiles_yaml_consistency

    diffs = verify_auth_profiles_yaml_consistency(ROOT)
    assert diffs == [], (
        "configs/auth_profiles.yaml has structural completeness violations:\n"
        + "\n".join(diffs)
    )


def test_auth_mode_expectations_derived_from_yaml() -> None:
    """AUTH_MODE_EXPECTATIONS must be derived from configs/auth_profiles.yaml, not hardcoded."""
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


# ---------------------------------------------------------------------------
# D. env examples contain required keys (derived from exposure/model contracts)
# ---------------------------------------------------------------------------

def _env_required_model_runtime_keys() -> list[str]:
    """Return required env keys derived from the model serving configuration."""
    # Read from model_serving.yaml if available; otherwise use the known runtime set.
    model_serving_path = ROOT / "configs" / "model_serving.yaml"
    if not model_serving_path.exists():
        return [
            "EMBEDDING_KO_BASE_URL",
            "EMBEDDING_KO_MODEL",
            "EMBEDDING_KO_TIMEOUT_SECONDS",
            "EMBEDDING_KO_MAX_CONCURRENCY",
            "EMBEDDING_KO_QUEUE_TIMEOUT_SECONDS",
        ]
    data = yaml.safe_load(model_serving_path.read_text(encoding="utf-8"))
    runtimes = data.get("runtimes", {})
    keys = []
    for runtime_name, runtime in runtimes.items():
        if not isinstance(runtime, dict) or not runtime.get("enabled", True):
            continue
        prefix = runtime.get("env_prefix", "")
        if not prefix:
            continue
        for suffix in ("BASE_URL", "MODEL", "TIMEOUT_SECONDS", "MAX_CONCURRENCY", "QUEUE_TIMEOUT_SECONDS"):
            key = f"{prefix}_{suffix}"
            if key not in keys:
                keys.append(key)
    return keys if keys else [
        "EMBEDDING_KO_BASE_URL",
        "EMBEDDING_KO_MODEL",
        "EMBEDDING_KO_TIMEOUT_SECONDS",
        "EMBEDDING_KO_MAX_CONCURRENCY",
        "EMBEDDING_KO_QUEUE_TIMEOUT_SECONDS",
    ]


def _env_required_exposure_bind_keys() -> list[str]:
    """Return bind addr keys for host-published services, derived from source registries."""
    data = _load_exposure()
    services = _load_services()
    all_published: set[str] = set()
    for profile in data.get("profiles", {}).values():
        if isinstance(profile, dict):
            all_published.update(profile.get("host_published", []))
    keys = []
    for svc_name in sorted(all_published):
        svc = services.get(svc_name, {})
        bind_env = svc.get("host_env_bind", "")
        if bind_env and bind_env not in keys:
            keys.append(bind_env)
    return keys


def _check_env_file(path: Path, keys: list[str]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [k for k in keys if k not in text]


def test_env_example_contains_embedding_ko_keys() -> None:
    keys = _env_required_model_runtime_keys()
    embedding_ko_keys = [k for k in keys if k.startswith("EMBEDDING_KO_")]
    missing = _check_env_file(ROOT / ".env.example", embedding_ko_keys)
    assert missing == [], f".env.example missing embedding_ko runtime keys: {missing}"


def test_env_local_example_contains_embedding_ko_keys() -> None:
    keys = _env_required_model_runtime_keys()
    embedding_ko_keys = [k for k in keys if k.startswith("EMBEDDING_KO_")]
    missing = _check_env_file(ROOT / ".env.local.example", embedding_ko_keys)
    assert missing == [], f".env.local.example missing embedding_ko runtime keys: {missing}"


def test_env_compose_example_contains_embedding_ko_keys() -> None:
    keys = _env_required_model_runtime_keys()
    embedding_ko_keys = [k for k in keys if k.startswith("EMBEDDING_KO_")]
    missing = _check_env_file(ROOT / ".env.compose.example", embedding_ko_keys)
    assert missing == [], f".env.compose.example missing embedding_ko runtime keys: {missing}"


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


# ---------------------------------------------------------------------------
# E. bootstrap.sh auth mode application policy
# ---------------------------------------------------------------------------

def test_bootstrap_does_not_skip_any_named_auth_mode() -> None:
    """bootstrap.sh must apply all named profiles, not skip specific ones by name."""
    text = (ROOT / "scripts/build/bootstrap.sh").read_text(encoding="utf-8")
    # No profile name should be hardcoded as a skip condition
    # The only skip allowed is for AUTH_MODE=custom (operator-managed)
    auth_yaml = _load_auth()
    for mode in auth_yaml.get("profiles", {}):
        if mode == "custom":
            continue
        assert f'!= "{mode}"' not in text, (
            f"bootstrap.sh must not skip AUTH_MODE={mode}; "
            "all named profiles (except custom) should be applied explicitly"
        )


def test_bootstrap_applies_named_auth_modes_skips_only_custom() -> None:
    text = (ROOT / "scripts/build/bootstrap.sh").read_text(encoding="utf-8")
    assert '!= "custom"' in text, (
        "bootstrap.sh must skip only AUTH_MODE=custom; all named profiles should be applied"
    )


# ---------------------------------------------------------------------------
# F. auth-doctor: delegated/custom profiles require explicit evidence
# ---------------------------------------------------------------------------

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


def test_auth_doctor_rejects_local_open_in_non_local() -> None:
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

    findings = diagnose_auth(MockSettings(), ROOT)  # type: ignore[arg-type]
    assert any(f.code == "LOCAL_OPEN_FORBIDDEN_NON_LOCAL" and f.level == "FAIL" for f in findings)


def test_compose_preflight_rejects_non_local_local_open(monkeypatch) -> None:
    import importlib.util
    import pytest

    spec = importlib.util.spec_from_file_location("preflight_compose", ROOT / "scripts/compose/preflight_compose.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "local_open")
    with pytest.raises(SystemExit) as exc:
        module._phase1({"profiles": {"private_network": {"diagnostics": {}}}})

    assert "auth profile evidence" in str(exc.value)


def test_compose_preflight_reads_auth_mode_from_env_file(monkeypatch, tmp_path) -> None:
    import importlib.util
    import pytest

    spec = importlib.util.spec_from_file_location("preflight_compose_env_file", ROOT / "scripts/compose/preflight_compose.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=production\nAUTH_MODE=local_open\n", encoding="utf-8")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("ENV_FILE", str(env_file))
    with pytest.raises(SystemExit):
        module._phase1({"profiles": {"private_network": {"diagnostics": {}}}})


# ---------------------------------------------------------------------------
# G. EXPOSURE_AUDIENCE value validation and local_only bind conflict
# ---------------------------------------------------------------------------

def _make_local_settings():
    """Return a MockSettings with minimal local auth (not production-sensitive)."""
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
        app_env = "local"
        documentation = MockDocumentation()

    return MockSettings()


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


def test_compose_preflight_reads_exposure_from_env_file(monkeypatch, tmp_path) -> None:
    import importlib.util
    import pytest

    spec = importlib.util.spec_from_file_location("preflight_compose_exposure_env_file", ROOT / "scripts/compose/preflight_compose.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=local\nAUTH_MODE=local_open\nEXPOSURE_MODE=master_open\nEXPOSURE_AUDIENCE=banana\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EXPOSURE_MODE", raising=False)
    monkeypatch.delenv("EXPOSURE_AUDIENCE", raising=False)
    monkeypatch.setenv("ENV_FILE", str(env_file))

    with pytest.raises(SystemExit):
        module._phase1(_load_exposure())


def test_preflight_compose_wrapper_does_not_preload_root_dotenv() -> None:
    text = (ROOT / "scripts/compose/preflight_compose.sh").read_text(encoding="utf-8")
    assert "load_local_env .env" not in text
    assert 'ENV_FILE="${ENV_FILE:-.env}"' in text
    assert 'env ENV_FILE="$ENV_FILE"' in text


# ---------------------------------------------------------------------------
# H. validate_docs_exposure integration
# ---------------------------------------------------------------------------

def test_validate_docs_exposure_passes_on_current_state() -> None:
    """validate_docs_exposure must pass on the current docs and features/ state."""
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_docs_exposure import validate
    violations = validate(ROOT)
    assert violations == [], (
        "validate_docs_exposure found violations in current docs/features:\n"
        + "\n".join(violations)
    )
