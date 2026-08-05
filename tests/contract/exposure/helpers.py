"""auth/exposure profile source-of-truth 일관성에 대한 계약 테스트.

설계 원칙: 테스트는 특정 과거 실수의 mode 이름이 아니라 구조적 불변식과 정책
제약을 검증한다. canonical mode 이름이 바뀌어도, 테스트는 canonical_modes가
선언하는 값이 무엇이든 그 불변식을 계속 검증한다.

검증 항목:
- validator 통합을 통한 configs/exposure_profiles.yaml 구조적 불변식
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


EXPOSURE_PROFILES_YAML = ROOT / "configs" / "exposure_profiles.yaml"


def _load_exposure() -> dict:
    return yaml.safe_load(EXPOSURE_PROFILES_YAML.read_text(encoding="utf-8"))


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


__all__ = [name for name in globals() if not name.startswith("__")]
