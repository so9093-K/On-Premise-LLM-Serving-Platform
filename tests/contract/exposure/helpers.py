"""auth/exposure profile source-of-truth 일관성에 대한 계약 테스트.

설계 원칙: 테스트는 특정 과거 실수의 mode 이름이 아니라 구조적 불변식과 정책
제약을 검증한다. canonical mode 이름이 바뀌어도, 테스트는 canonical_modes가
선언하는 값이 무엇이든 그 불변식을 계속 검증한다.

검증 항목:
- validator 통합을 통한 configs/exposure_profiles.yaml 구조적 불변식
- configs/auth_profiles.yaml 완전성 (verify_auth_profiles_yaml_consistency 경유)
- auth_control.AUTH_MODE_EXPECTATIONS가 (별도 하드코딩 dict가 아니라) YAML에서 도출됨
- env 예시 파일이 env_contract 필수 키를 모두 포함함
- bootstrap.sh가 private_network 전용 skip 없이 named auth mode를 적용함
"""

from __future__ import annotations

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
    """model serving 설정에서 도출한 필수 env 키 목록을 반환한다."""
    # model_serving.yaml이 있으면 거기서 읽고, 없으면 알려진 runtime 집합을 쓴다.
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
    """source registry에서 도출한, host-published 서비스들의 bind addr 키를 반환한다."""
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
    """최소한의 local auth를 가진 MockSettings를 반환한다(운영 민감 정보 아님)."""
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
