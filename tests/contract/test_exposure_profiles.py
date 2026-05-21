"""Contract tests for auth/exposure profile source-of-truth consistency.

Design principle: tests verify structural invariants and policy constraints,
not specific past-mistake mode names. If canonical mode names change,
tests continue to verify the invariants against whatever canonical_modes declares.

Verifies:
- configs/exposure_profiles.yaml structural invariants (via validate_exposure_profiles.py logic)
- configs/auth_profiles.yaml completeness (via verify_auth_profiles_yaml_consistency)
- auth_control.AUTH_MODE_EXPECTATIONS is derived from YAML (not a separate hardcoded dict)
- default_private profile does not expose internal/diagnostic services
- diagnostic_full_stack profile exposes all expected service categories
- EXPOSURE_AUDIENCE is required for diagnostic_full_stack profiles
- deprecated_aliases are structurally valid
- compose override exists for each canonical non-base mode
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
OVERRIDES_DIR = ROOT / "ops" / "compose" / "overrides"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_exposure() -> dict:
    return yaml.safe_load(EXPOSURE_PROFILES_YAML.read_text(encoding="utf-8"))


def _load_auth() -> dict:
    return yaml.safe_load(AUTH_PROFILES_YAML.read_text(encoding="utf-8"))


def _canonical_modes(data: dict) -> list[str]:
    return data.get("canonical_modes", [])


def _profile(data: dict, mode: str) -> dict:
    return data.get("profiles", {}).get(mode, {})


def _class_profiles(data: dict, cls: str) -> list[str]:
    return [m for m, p in data.get("profiles", {}).items() if isinstance(p, dict) and p.get("class") == cls]


# ---------------------------------------------------------------------------
# A. configs/exposure_profiles.yaml structure
# ---------------------------------------------------------------------------

def test_exposure_profiles_yaml_exists() -> None:
    assert EXPOSURE_PROFILES_YAML.exists(), "configs/exposure_profiles.yaml must exist"


def test_exposure_profiles_has_canonical_modes() -> None:
    data = _load_exposure()
    modes = _canonical_modes(data)
    assert len(modes) >= 1, "canonical_modes must be non-empty"


def test_exposure_canonical_modes_match_profiles_exactly() -> None:
    data = _load_exposure()
    canonical = set(_canonical_modes(data))
    profile_keys = set(data.get("profiles", {}).keys())
    assert canonical == profile_keys, (
        f"canonical_modes and profiles.keys() must match exactly.\n"
        f"In canonical_modes but not profiles: {sorted(canonical - profile_keys)}\n"
        f"In profiles but not canonical_modes: {sorted(profile_keys - canonical)}"
    )


def test_exposure_deprecated_aliases_do_not_overlap_profiles() -> None:
    data = _load_exposure()
    aliases = set(data.get("deprecated_aliases", {}).keys())
    profiles = set(data.get("profiles", {}).keys())
    overlap = aliases & profiles
    assert overlap == set(), (
        f"deprecated_aliases must not name the same modes as profiles: {sorted(overlap)}"
    )


def test_exposure_deprecated_aliases_have_required_fields() -> None:
    data = _load_exposure()
    for alias_name, alias in data.get("deprecated_aliases", {}).items():
        for field in ("target", "reason", "remove_after"):
            assert field in alias, (
                f"deprecated_aliases.{alias_name} missing required field: {field!r}"
            )
        target = alias.get("target")
        canonical = _canonical_modes(data)
        assert target in canonical, (
            f"deprecated_aliases.{alias_name}.target={target!r} is not in canonical_modes={canonical}"
        )


def test_exposure_profiles_have_required_fields() -> None:
    data = _load_exposure()
    for mode in _canonical_modes(data):
        profile = _profile(data, mode)
        for field in ("class", "diagnostics", "host_published", "description"):
            assert field in profile, f"profiles.{mode} missing required field: {field!r}"


def test_exposure_diagnostics_fields_complete() -> None:
    required_fields = (
        "gateway_bypass_possible",
        "direct_model_runtime_access",
        "direct_risk_adapter_access",
        "direct_operations_endpoints",
        "requires_exposure_audience",
    )
    data = _load_exposure()
    for mode in _canonical_modes(data):
        diag = _profile(data, mode).get("diagnostics", {})
        for field in required_fields:
            assert field in diag, f"profiles.{mode}.diagnostics missing field: {field!r}"


# ---------------------------------------------------------------------------
# B. default_private class invariants
# ---------------------------------------------------------------------------

def test_exactly_one_default_private_profile() -> None:
    data = _load_exposure()
    modes = _class_profiles(data, "default_private")
    assert len(modes) == 1, (
        f"Expected exactly 1 profile with class=default_private, found {len(modes)}: {modes}"
    )


def test_default_private_does_not_expose_internal_services() -> None:
    """default_private must not host-publish services that bypass Gateway auth."""
    data = _load_exposure()
    forbidden = {"main_llm_vllm", "embedding_vllm", "embedding_ko_vllm", "risk_prompt_vllm",
                 "risk_adapter", "prometheus", "dcgm_exporter", "cadvisor"}
    for mode in _class_profiles(data, "default_private"):
        published = set(_profile(data, mode).get("host_published", []))
        exposed = published & forbidden
        assert exposed == set(), (
            f"profiles.{mode} (default_private) must not host-publish: {sorted(exposed)}"
        )


def test_default_private_diagnostics_are_all_false() -> None:
    data = _load_exposure()
    dangerous = ("gateway_bypass_possible", "direct_model_runtime_access",
                 "direct_risk_adapter_access", "direct_operations_endpoints")
    for mode in _class_profiles(data, "default_private"):
        diag = _profile(data, mode).get("diagnostics", {})
        for field in dangerous:
            assert not diag.get(field), (
                f"profiles.{mode} (default_private) must have diagnostics.{field}=false"
            )


def test_default_private_does_not_require_exposure_audience() -> None:
    data = _load_exposure()
    for mode in _class_profiles(data, "default_private"):
        diag = _profile(data, mode).get("diagnostics", {})
        assert not diag.get("requires_exposure_audience"), (
            f"profiles.{mode} (default_private) must not require EXPOSURE_AUDIENCE"
        )


# ---------------------------------------------------------------------------
# C. diagnostic_full_stack class invariants
# ---------------------------------------------------------------------------

def test_exactly_one_diagnostic_full_stack_profile() -> None:
    data = _load_exposure()
    modes = _class_profiles(data, "diagnostic_full_stack")
    assert len(modes) == 1, (
        f"Expected exactly 1 profile with class=diagnostic_full_stack, found {len(modes)}: {modes}"
    )


def test_diagnostic_full_stack_exposes_vllm_runtimes() -> None:
    data = _load_exposure()
    required = {"main_llm_vllm", "embedding_vllm", "embedding_ko_vllm", "risk_prompt_vllm"}
    for mode in _class_profiles(data, "diagnostic_full_stack"):
        published = set(_profile(data, mode).get("host_published", []))
        missing = required - published
        assert missing == set(), (
            f"profiles.{mode} (diagnostic_full_stack) must host-publish all vLLM runtimes. Missing: {sorted(missing)}"
        )


def test_diagnostic_full_stack_exposes_risk_adapter() -> None:
    data = _load_exposure()
    for mode in _class_profiles(data, "diagnostic_full_stack"):
        published = set(_profile(data, mode).get("host_published", []))
        assert "risk_adapter" in published, (
            f"profiles.{mode} (diagnostic_full_stack) must host-publish risk_adapter"
        )


def test_diagnostic_full_stack_exposes_operations_metrics() -> None:
    data = _load_exposure()
    required = {"prometheus", "dcgm_exporter", "cadvisor"}
    for mode in _class_profiles(data, "diagnostic_full_stack"):
        published = set(_profile(data, mode).get("host_published", []))
        missing = required - published
        assert missing == set(), (
            f"profiles.{mode} (diagnostic_full_stack) must host-publish operations metrics services. Missing: {sorted(missing)}"
        )


def test_diagnostic_full_stack_has_gateway_bypass_diagnostic() -> None:
    data = _load_exposure()
    for mode in _class_profiles(data, "diagnostic_full_stack"):
        diag = _profile(data, mode).get("diagnostics", {})
        assert diag.get("gateway_bypass_possible"), (
            f"profiles.{mode} (diagnostic_full_stack) must have diagnostics.gateway_bypass_possible=true"
        )


def test_diagnostic_full_stack_requires_exposure_audience() -> None:
    data = _load_exposure()
    for mode in _class_profiles(data, "diagnostic_full_stack"):
        diag = _profile(data, mode).get("diagnostics", {})
        assert diag.get("requires_exposure_audience"), (
            f"profiles.{mode} (diagnostic_full_stack) must have diagnostics.requires_exposure_audience=true"
        )


# ---------------------------------------------------------------------------
# D. Compose override files for canonical non-base modes
# ---------------------------------------------------------------------------

def test_compose_override_exists_for_each_canonical_non_base_mode() -> None:
    data = _load_exposure()
    base_modes = _class_profiles(data, "default_private")
    base_mode = base_modes[0] if base_modes else None
    for mode in _canonical_modes(data):
        if mode == base_mode:
            continue
        slug = mode.replace("_", "-")
        override = OVERRIDES_DIR / f"exposure.{slug}.yaml"
        assert override.exists(), (
            f"compose override file must exist for canonical mode {mode!r}: {override}"
        )


def test_compose_override_for_diagnostic_full_stack_references_source_of_truth() -> None:
    data = _load_exposure()
    for mode in _class_profiles(data, "diagnostic_full_stack"):
        slug = mode.replace("_", "-")
        override = OVERRIDES_DIR / f"exposure.{slug}.yaml"
        if not override.exists():
            continue
        text = override.read_text(encoding="utf-8")
        assert "configs/exposure_profiles.yaml" in text, (
            f"exposure.{slug}.yaml must reference configs/exposure_profiles.yaml as source"
        )


def test_compose_override_for_diagnostic_full_stack_contains_vllm_services() -> None:
    """Verify the override file actually contains compose service entries for vLLM runtimes."""
    data = _load_exposure()
    services = data.get("services", {})
    for mode in _class_profiles(data, "diagnostic_full_stack"):
        slug = mode.replace("_", "-")
        override = OVERRIDES_DIR / f"exposure.{slug}.yaml"
        if not override.exists():
            continue
        override_text = override.read_text(encoding="utf-8")
        profile = _profile(data, mode)
        published = set(profile.get("host_published", []))
        vllm_svcs = {"main_llm_vllm", "embedding_vllm", "embedding_ko_vllm", "risk_prompt_vllm"}
        for svc_name in vllm_svcs & published:
            svc_info = services.get(svc_name, {})
            compose_svc = svc_info.get("compose_service", svc_name.replace("_", "-"))
            assert compose_svc in override_text, (
                f"exposure.{slug}.yaml must contain service {compose_svc!r} "
                f"(profiles.{mode}.host_published includes {svc_name!r})"
            )


# ---------------------------------------------------------------------------
# E. resolve_exposure_mode.py invariants
# ---------------------------------------------------------------------------

def test_resolve_exposure_mode_returns_canonical_for_canonical() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.compose.resolve_exposure_mode import load_exposure_data, resolve
    data = load_exposure_data(ROOT)
    for mode in _canonical_modes(data):
        canonical, warning = resolve(mode, data)
        assert canonical == mode, f"resolve({mode!r}) should return canonical mode, got {canonical!r}"
        assert warning is None, f"resolve({mode!r}) should have no warning for canonical mode"


def test_resolve_exposure_mode_handles_deprecated_aliases() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.compose.resolve_exposure_mode import load_exposure_data, resolve
    data = load_exposure_data(ROOT)
    for alias_name, alias in data.get("deprecated_aliases", {}).items():
        target = alias["target"]
        canonical, warning = resolve(alias_name, data)
        assert canonical == target, f"resolve({alias_name!r}) should return target={target!r}, got {canonical!r}"
        assert warning is not None, f"resolve({alias_name!r}) should emit a deprecation warning"


def test_resolve_exposure_mode_fails_on_unknown_mode() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.compose.resolve_exposure_mode import load_exposure_data, resolve
    import pytest
    data = load_exposure_data(ROOT)
    with pytest.raises(SystemExit) as exc_info:
        resolve("__nonexistent_mode__", data)
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# F. validate_exposure_profiles.py structural validator
# ---------------------------------------------------------------------------

def test_validate_exposure_profiles_passes_on_current_yaml() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_exposure_profiles import load, validate
    data = load(EXPOSURE_PROFILES_YAML)
    violations = validate(data, strict=False)
    assert violations == [], (
        "validate_exposure_profiles found structural violations:\n" + "\n".join(violations)
    )


def test_validate_exposure_profiles_strict_passes_on_current_yaml() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.validation.validate_exposure_profiles import load, validate
    data = load(EXPOSURE_PROFILES_YAML)
    violations = validate(data, strict=True)
    assert violations == [], (
        "validate_exposure_profiles (strict) found violations:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# G. configs/auth_profiles.yaml — YAML is actual source of truth
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
# H. env examples contain required keys (derived from exposure/model contracts)
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
    """Return bind addr keys for host-published services, derived from exposure_profiles.yaml."""
    data = _load_exposure()
    services = data.get("services", {})
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
        assert missing == [], f"{example} missing bind addr keys derived from exposure_profiles.yaml: {missing}"


# ---------------------------------------------------------------------------
# I. bootstrap.sh auth mode application policy
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
# J. auth-doctor: internal_trusted + non-local APP_ENV is INFO not FAIL
# ---------------------------------------------------------------------------

def test_auth_doctor_internal_trusted_not_fail() -> None:
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

    findings = diagnose_auth(MockSettings(), ROOT)  # type: ignore[arg-type]

    fail_findings = [f for f in findings if f.level == "FAIL"]
    # EXPOSURE_AUDIENCE_MISSING is allowed — that's from EXPOSURE_MODE env, not auth_mode
    auth_fail_findings = [f for f in fail_findings if f.code not in ("EXPOSURE_AUDIENCE_MISSING", "EXPOSURE_PUBLIC_AUDIENCE_WITHOUT_EXPLICIT_OPT_IN")]
    assert auth_fail_findings == [], (
        "auth-doctor must not FAIL for AUTH_MODE=internal_trusted + APP_ENV=production; "
        f"found FAIL findings: {[f.code for f in auth_fail_findings]}"
    )
    info_findings = [f for f in findings if f.code == "AUTH_DELEGATED_TO_NETWORK"]
    assert info_findings, (
        "auth-doctor must produce AUTH_DELEGATED_TO_NETWORK INFO for "
        "AUTH_MODE=internal_trusted + APP_ENV=production"
    )


# ---------------------------------------------------------------------------
# K. EXPOSURE_AUDIENCE value validation and local_only bind conflict
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


def test_auth_doctor_rejects_local_only_with_open_bind(monkeypatch) -> None:
    """auth-doctor must FAIL when local_only is declared but services bind to 0.0.0.0."""
    sys.path.insert(0, str(ROOT / "src"))
    from ai_model_serving.auth_control import diagnose_auth

    monkeypatch.setenv("EXPOSURE_MODE", "master_open")
    monkeypatch.setenv("EXPOSURE_AUDIENCE", "local_only")
    # Default bind is 0.0.0.0 for most services — do not override, so conflict is detected

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

    data = _load_exposure()
    services = data.get("services", {})

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


# ---------------------------------------------------------------------------
# L. validate_docs_exposure: feature manifest scanning
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


def test_exposure_audience_allowed_values_covers_security_profiles_yaml() -> None:
    """features/security_profiles.yaml must not mention deprecated aliases outside deprecated sections."""
    data = _load_exposure()
    aliases = set(data.get("deprecated_aliases", {}).keys())
    manifest = ROOT / "features" / "security_profiles.yaml"
    if not manifest.exists():
        return
    lines = manifest.read_text(encoding="utf-8").splitlines()

    in_deprecated_block = False
    deprecated_block_indent = -1
    violations = []
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped and not stripped.startswith("-"):
            key = stripped.split(":")[0].strip()
            indent = len(line) - len(line.lstrip())
            if "deprecated" in key.lower():
                in_deprecated_block = True
                deprecated_block_indent = indent
            elif indent <= deprecated_block_indent:
                in_deprecated_block = False
                deprecated_block_indent = -1
        if in_deprecated_block:
            continue
        for alias in aliases:
            if alias in line and "deprecated" not in line.lower():
                violations.append(f"line {lineno}: deprecated alias {alias!r} outside deprecated section: {line.rstrip()}")

    assert violations == [], (
        "features/security_profiles.yaml must not reference deprecated aliases outside deprecated sections:\n"
        + "\n".join(violations)
    )
