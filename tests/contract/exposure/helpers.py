from __future__ import annotations

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


import sys


from pathlib import Path


import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "VERSION").exists() and (parent / "configs").exists():
            return parent
    raise RuntimeError("repository root not found")


ROOT = _repo_root()


AUTH_PROFILES_YAML = ROOT / "configs" / "auth_profiles.yaml"


EXPOSURE_PROFILES_YAML = ROOT / "configs" / "exposure_profiles.yaml"


SERVICES_YAML = ROOT / "configs" / "services.yaml"


def _load_exposure() -> dict:
    return yaml.safe_load(EXPOSURE_PROFILES_YAML.read_text(encoding="utf-8"))


def _load_auth() -> dict:
    return yaml.safe_load(AUTH_PROFILES_YAML.read_text(encoding="utf-8"))


def _load_services() -> dict:
    return yaml.safe_load(SERVICES_YAML.read_text(encoding="utf-8")).get("services", {})


def _canonical_modes(data: dict) -> list[str]:
    return data.get("canonical_modes", [])


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


__all__ = [name for name in globals() if not name.startswith("__")]
