#!/usr/bin/env python3
"""configs/exposure_profiles.yaml의 구조적 불변식을 검증한다.

체크 항목:
- canonical_modes 필드가 존재하고 비어있지 않은지
- profiles.keys()가 canonical_modes와 정확히 일치하는지
- 각 프로필이 class, diagnostics, host_published를 갖는지
- class: default_private인 프로필이 정확히 1개인지
- class: diagnostic_full_stack인 프로필이 정확히 1개인지
- default_private 프로필이 차단된 서비스 카테고리를 노출하지 않는지
- diagnostic_full_stack 프로필이 필수 서비스 카테고리와 모든 model runtime을 커버하는지
- configs/services.yaml이 profiles.host_published가 참조하는 모든 서비스를 커버하는지
- 생성된 compose/diagnostics 소비자를 위해 서비스 레지스트리 필드가 완전한지

생성된 compose override의 drift는 별도로 아래에서 검사한다:
  PYTHONPATH=src python scripts/compose/render_exposure_overrides.py --check

사용법:
  PYTHONPATH=src python scripts/validation/validate_exposure_profiles.py
  PYTHONPATH=src python scripts/validation/validate_exposure_profiles.py --strict
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

try:
    import yaml
except ModuleNotFoundError:
    raise SystemExit("Missing dependency: PyYAML.")

_DEFAULT_PRIVATE_BLOCKED_CATEGORIES = (
    "model_runtime",
    "risk_adapter",
    "operations_endpoint",
)
_DIAGNOSTIC_REQUIRED_CATEGORY_COVERAGE = (
    "gateway",
    "model_runtime",
    "risk_adapter",
    "operations_endpoint",
    "visualization",
)

# 프로필별 필수 필드
_PROFILE_REQUIRED_FIELDS = ("class", "diagnostics", "host_published", "description")

# 필수 diagnostics boolean 필드
_DIAGNOSTICS_FIELDS = (
    "gateway_bypass_possible",
    "direct_model_runtime_access",
    "direct_risk_adapter_access",
    "direct_operations_endpoints",
    "requires_exposure_audience",
)

_SERVICE_REQUIRED_FIELDS = (
    "compose_service",
    "container_port",
    "host_env_port",
    "default_host_port",
    "host_env_bind",
    "default_bind",
    "categories",
)


def _published_compose_services(document: object) -> set[str]:
    """Return services that actually declare a non-empty host-port mapping.

    `ports: []` is deliberately treated as private.  The exposure profile is the
    source of truth; this check only verifies that the base compose file and the
    generated override project that intent without requiring Docker Compose.
    """
    if not isinstance(document, dict):
        return set()
    services = document.get("services", {})
    if not isinstance(services, dict):
        return set()
    return {
        str(name)
        for name, service in services.items()
        if isinstance(service, dict) and bool(service.get("ports"))
    }


def validate_compose_exposure_projection(data: dict, services: dict) -> list[str]:
    """Ensure checked-in compose port mappings equal the exposure profiles.

    This replaces tests that invoked `docker compose config` only to compare
    published service names.  Value-level override drift remains owned by
    render_exposure_overrides.py --check, which renders from the same registry.
    """
    profiles = data.get("profiles", {})
    base_modes = [
        mode
        for mode, profile in profiles.items()
        if isinstance(profile, dict) and profile.get("class") == "default_private"
    ]
    if len(base_modes) != 1:
        return []  # validate() reports the malformed profile definition.

    base_mode = base_modes[0]
    base_profile = profiles[base_mode]
    expected_base = {
        str(services[name]["compose_service"])
        for name in base_profile.get("host_published", [])
        if name in services
    }
    base_compose_path = ROOT / "ops" / "compose" / "full-stack.private-network.yaml"
    try:
        base_compose = yaml.safe_load(base_compose_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"cannot read base compose exposure projection: {exc}"]

    violations: list[str] = []
    actual_base = _published_compose_services(base_compose)
    if actual_base != expected_base:
        violations.append(
            "base compose host-published services disagree with "
            f"profiles.{base_mode}.host_published: expected={sorted(expected_base)}, "
            f"actual={sorted(actual_base)}"
        )

    base_published = set(base_profile.get("host_published", []))
    for mode, profile in profiles.items():
        if mode == base_mode or not isinstance(profile, dict):
            continue
        expected_extra = {
            str(services[name]["compose_service"])
            for name in set(profile.get("host_published", [])) - base_published
            if name in services
        }
        override = ROOT / "ops" / "compose" / "overrides" / f"exposure.{mode.replace('_', '-')}.yaml"
        try:
            override_doc = yaml.safe_load(override.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            violations.append(f"cannot read {override.relative_to(ROOT)}: {exc}")
            continue
        actual_extra = _published_compose_services(override_doc)
        if actual_extra != expected_extra:
            violations.append(
                f"{override.relative_to(ROOT)} host-published services disagree with "
                f"profiles.{mode}.host_published: expected={sorted(expected_extra)}, "
                f"actual={sorted(actual_extra)}"
            )
    return violations


def load(path: Path) -> dict:
    if not path.exists():
        print(f"FAIL: configs/exposure_profiles.yaml not found at {path}", file=sys.stderr)
        raise SystemExit(1)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print("FAIL: configs/exposure_profiles.yaml is not a YAML mapping", file=sys.stderr)
        raise SystemExit(1)
    return data


def load_services(path: Path) -> dict:
    if not path.exists():
        print(f"FAIL: configs/services.yaml not found at {path}", file=sys.stderr)
        raise SystemExit(1)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        print("FAIL: configs/services.yaml must contain a services mapping", file=sys.stderr)
        raise SystemExit(1)
    return data["services"]


def _service_categories(service: object) -> set[str]:
    if not isinstance(service, dict):
        return set()
    categories = service.get("categories", [])
    return {str(category) for category in categories} if isinstance(categories, list) else set()


def _services_with_category(services: dict, category: str) -> set[str]:
    return {
        service_name
        for service_name, service in services.items()
        if category in _service_categories(service)
    }


def validate(data: dict, strict: bool = False, services: dict | None = None) -> list[str]:
    """위반 메시지 목록을 반환한다. 비어있으면 유효하다는 뜻."""
    violations: list[str] = []

    # 1. canonical_modes 필드
    canonical_modes: list[str] = data.get("canonical_modes", [])
    if not canonical_modes:
        violations.append("canonical_modes field is missing or empty")
        return violations  # 더 이상 의미 있게 진행할 수 없음

    # 2. profiles.keys()는 canonical_modes와 정확히 일치해야 함
    profiles: dict = data.get("profiles", {})
    if "services" in data:
        violations.append(
            "configs/exposure_profiles.yaml must not define services; use configs/services.yaml "
            "for compose service/port/bind mapping"
        )
    profile_names = set(profiles.keys())
    canonical_set = set(canonical_modes)
    if profile_names != canonical_set:
        extra = profile_names - canonical_set
        missing = canonical_set - profile_names
        if extra:
            violations.append(f"profiles has keys not in canonical_modes: {sorted(extra)}")
        if missing:
            violations.append(f"canonical_modes has modes not in profiles: {sorted(missing)}")

    # 3. 각 프로필은 필수 필드와 유효한 class를 가져야 함
    classes_found: dict[str, list[str]] = {}
    for mode, profile in profiles.items():
        if not isinstance(profile, dict):
            violations.append(f"profiles.{mode} is not a mapping")
            continue
        for field in _PROFILE_REQUIRED_FIELDS:
            if field not in profile:
                violations.append(f"profiles.{mode} missing required field: {field!r}")

        # diagnostics 블록 완전성
        diag = profile.get("diagnostics", {})
        if not isinstance(diag, dict):
            violations.append(f"profiles.{mode}.diagnostics is not a mapping")
        else:
            for df in _DIAGNOSTICS_FIELDS:
                if df not in diag:
                    violations.append(f"profiles.{mode}.diagnostics missing field: {df!r}")

        # class 필드
        cls = profile.get("class", "")
        classes_found.setdefault(cls, []).append(mode)

    # 4. default_private와 diagnostic_full_stack은 각각 정확히 1개여야 함
    default_private_modes = classes_found.get("default_private", [])
    diagnostic_full_stack_modes = classes_found.get("diagnostic_full_stack", [])

    if len(default_private_modes) != 1:
        violations.append(
            f"Expected exactly 1 profile with class=default_private, found {len(default_private_modes)}: {default_private_modes}"
        )
    if len(diagnostic_full_stack_modes) != 1:
        violations.append(
            f"Expected exactly 1 profile with class=diagnostic_full_stack, found {len(diagnostic_full_stack_modes)}: {diagnostic_full_stack_modes}"
        )

    # 5. 서비스 레지스트리가 참조되는 모든 서비스명과 카테고리를 포함하는지 확인
    services = services if services is not None else load_services(ROOT / "configs" / "services.yaml")
    for svc_name, service in services.items():
        if not isinstance(service, dict):
            violations.append(f"services.{svc_name} is not a mapping")
            continue
        for field in _SERVICE_REQUIRED_FIELDS:
            if field not in service:
                violations.append(f"services.{svc_name} missing required field: {field!r}")
        categories = service.get("categories")
        if not isinstance(categories, list) or not categories:
            violations.append(f"services.{svc_name}.categories must be a non-empty list")
        elif any(not isinstance(category, str) or not category for category in categories):
            violations.append(f"services.{svc_name}.categories must contain non-empty strings")
    for mode, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        for svc in profile.get("host_published", []):
            if svc not in services:
                violations.append(
                    f"profiles.{mode}.host_published references service {svc!r} not defined in configs/services.yaml"
                )

    # 6. default_private는 차단된 서비스 카테고리를 노출하면 안 됨
    for mode in default_private_modes:
        profile = profiles.get(mode, {})
        published = set(profile.get("host_published", []))
        for category in _DEFAULT_PRIVATE_BLOCKED_CATEGORIES:
            blocked = sorted(published & _services_with_category(services, category))
            if blocked:
                violations.append(
                    f"default_private profile must not host-publish {category} services: {', '.join(blocked)}"
                )
        diag = profile.get("diagnostics", {})
        for dangerous in ("gateway_bypass_possible", "direct_model_runtime_access", "direct_risk_adapter_access", "direct_operations_endpoints"):
            if diag.get(dangerous):
                violations.append(
                    f"profiles.{mode} (default_private) has diagnostics.{dangerous}=true — not allowed for default_private class"
                )

    # 7. diagnostic_full_stack은 카테고리 커버리지와 모든 model runtime을 노출해야 함
    for mode in diagnostic_full_stack_modes:
        profile = profiles.get(mode, {})
        published = set(profile.get("host_published", []))
        for category in _DIAGNOSTIC_REQUIRED_CATEGORY_COVERAGE:
            category_services = _services_with_category(services, category)
            if not published & category_services:
                violations.append(
                    f"diagnostic_full_stack profile must host-publish at least one {category} service"
                )
        missing_model_runtimes = sorted(_services_with_category(services, "model_runtime") - published)
        if missing_model_runtimes:
            violations.append(
                "diagnostic_full_stack profile is missing model_runtime services: "
                + ", ".join(missing_model_runtimes)
            )
        diag = profile.get("diagnostics", {})
        for required_diag in ("gateway_bypass_possible", "direct_model_runtime_access", "direct_operations_endpoints"):
            if not diag.get(required_diag):
                violations.append(
                    f"profiles.{mode} (diagnostic_full_stack) must have diagnostics.{required_diag}=true"
                )
        if not diag.get("requires_exposure_audience"):
            violations.append(
                f"profiles.{mode} (diagnostic_full_stack) must have diagnostics.requires_exposure_audience=true"
            )

    # 8. 서비스 레지스트리 port 필드는 숫자 형식을 유지해야 함
    for svc_name, service in services.items():
        if not isinstance(service, dict):
            continue
        for field in ("container_port", "default_host_port"):
            try:
                int(service.get(field, -1))
            except (TypeError, ValueError):
                violations.append(f"services.{svc_name}.{field} must be numeric")

    return violations


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Validate configs/exposure_profiles.yaml structural invariants.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="확장된 source-of-truth 불변식을 검증합니다; 생성된 override의 drift는 render_exposure_overrides.py --check가 검사합니다.",
    )
    args = parser.parse_args()

    data = load(ROOT / "configs" / "exposure_profiles.yaml")
    services = load_services(ROOT / "configs" / "services.yaml")
    violations = validate(data, strict=args.strict, services=services)
    if args.strict and not violations:
        violations.extend(validate_compose_exposure_projection(data, services))

    if violations:
        for v in violations:
            print(f"FAIL: {v}", file=sys.stderr)
        print(f"\nvalidate_exposure_profiles: {len(violations)} violation(s) found.", file=sys.stderr)
        return 1

    print("validate_exposure_profiles: OK — configs/exposure_profiles.yaml is structurally valid.")
    if args.strict:
        print("  (strict mode: extended source-of-truth invariants verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
