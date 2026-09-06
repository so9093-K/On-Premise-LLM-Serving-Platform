#!/usr/bin/env python3
"""`.env` 예시 파일들이 configs/env_contract.yaml에 선언된 키를 전부 갖고 있는지 검증한다.

체크 항목:
- env_contract.yaml에 선언된 env example이 각각 필요한 키 집합을 포함하는지
- 필요한 키 집합: 공통 예시 키, 인증 키, runtime override 키, exposure 키
- non-base exposure profile에 필요한 example key가 선언되어 있는지

사용법:
  python scripts/validation/validate_env_contract.py
  python scripts/validation/validate_env_contract.py --strict
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

try:
    import yaml
except ModuleNotFoundError:
    raise SystemExit("Missing dependency: PyYAML.")

from ai_model_serving.settings_parts.dotenv_parser import parse_env_file  # noqa: E402
from ai_model_serving.auth_control import auth_profile_env_values  # noqa: E402


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Expected YAML mapping at {path}")
    return data


def expand_required_keys(
    contract: dict[str, Any],
    key_set_names: list[str],
    *,
    violations: list[str],
) -> list[str]:
    """env_contract.yaml의 key_set 참조 목록을 실제 키 이름들로 펼친다."""
    required: list[str] = []

    for name in key_set_names:
        if not isinstance(name, str) or not name:
            violations.append(
                "env_contract.yaml: required_key_sets entries must be non-empty strings"
            )
            continue
        if name in {"common_example_keys", "auth_mode_keys", "auth_evidence_keys"}:
            values = contract.get(name)
            if not isinstance(values, list):
                continue  # validate_contract_structure() reports the malformed block.
            required.extend(values)
        elif name == "runtime_override_example_keys":
            runtime_overrides = contract.get("runtime_override_example_keys")
            if not isinstance(runtime_overrides, dict):
                continue  # validate_contract_structure() reports the malformed block.
            for runtime_cfg in runtime_overrides.values():
                if not isinstance(runtime_cfg, dict):
                    continue
                prefix = runtime_cfg.get("env_prefix")
                suffixes = runtime_cfg.get("suffixes")
                if not isinstance(prefix, str) or not isinstance(suffixes, list):
                    continue
                for suffix in suffixes:
                    if not isinstance(suffix, str):
                        continue
                    required.append(f"{prefix}_{suffix}")
        elif name.startswith("exposure_mode_requirements."):
            sub = name.split(".", 1)[1]
            mode_requirements = contract.get("exposure_mode_requirements")
            values = mode_requirements.get(sub) if isinstance(mode_requirements, dict) else None
            if not isinstance(values, list):
                violations.append(
                    f"env_contract.yaml: required key set {name!r} does not exist or is not a list"
                )
                continue
            required.extend(values)
        else:
            # contract 최상위의 직접 목록
            val = contract.get(name)
            if isinstance(val, list):
                required.extend(val)
            else:
                violations.append(
                    f"env_contract.yaml: required key set {name!r} does not exist or is not a list"
                )

    return required


def _string_list(value: Any, *, label: str, violations: list[str]) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        violations.append(f"env_contract.yaml: {label} must be a non-empty string list")
        return []
    if len(set(value)) != len(value):
        violations.append(f"env_contract.yaml: {label} contains duplicate keys")
    return value


def validate_contract_structure(contract: dict[str, Any]) -> list[str]:
    """필수 contract 블록이 사라져 검사가 조용히 축소되지 않게 한다."""
    violations: list[str] = []
    for name in ("common_example_keys", "auth_mode_keys", "auth_evidence_keys"):
        _string_list(contract.get(name), label=name, violations=violations)

    runtime_overrides = contract.get("runtime_override_example_keys")
    if not isinstance(runtime_overrides, dict) or not runtime_overrides:
        violations.append(
            "env_contract.yaml: runtime_override_example_keys must be a non-empty mapping"
        )
    else:
        for name, raw in runtime_overrides.items():
            label = f"runtime_override_example_keys.{name}"
            if not isinstance(raw, dict):
                violations.append(f"env_contract.yaml: {label} must be a mapping")
                continue
            if not isinstance(raw.get("env_prefix"), str) or not raw["env_prefix"]:
                violations.append(f"env_contract.yaml: {label}.env_prefix must be non-empty")
            _string_list(raw.get("suffixes"), label=f"{label}.suffixes", violations=violations)

    env_examples = contract.get("env_examples")
    if not isinstance(env_examples, dict) or not env_examples:
        violations.append("env_contract.yaml: env_examples must be a non-empty mapping")
    else:
        for filename, raw in env_examples.items():
            label = f"env_examples.{filename}"
            if (
                not isinstance(filename, str)
                or Path(filename).name != filename
                or not filename.endswith(".example")
            ):
                violations.append(
                    f"env_contract.yaml: {label} must use a top-level .example filename"
                )
            if not isinstance(raw, dict):
                violations.append(f"env_contract.yaml: {label} must be a mapping")
                continue
            _string_list(
                raw.get("required_key_sets"),
                label=f"{label}.required_key_sets",
                violations=violations,
            )

    return violations


def validate_service_env_projections(root: Path, contract: dict[str, Any]) -> list[str]:
    """Verify target-scoped process-env projections without reading secret values."""
    violations: list[str] = []
    projections = contract.get("service_env_projections")
    if not isinstance(projections, dict) or not projections:
        return [
            "env_contract.yaml: service_env_projections must be a non-empty mapping"
        ]

    targets_path = root / "configs" / "deployment_targets.yaml"
    targets_document = load_yaml(targets_path) if targets_path.exists() else {}
    targets = targets_document.get("targets") if isinstance(targets_document.get("targets"), dict) else {}
    projected_targets: set[str] = set()
    for name, raw in projections.items():
        label = f"service_env_projections.{name}"
        if not isinstance(raw, dict):
            violations.append(f"env_contract.yaml: {label} must be a mapping")
            continue
        target = raw.get("deployment_target")
        target_cfg = targets.get(target) if isinstance(target, str) else None
        if not isinstance(target_cfg, dict):
            violations.append(f"env_contract.yaml: {label}.deployment_target is unknown: {target!r}")
            continue
        if target in projected_targets:
            violations.append(f"env_contract.yaml: duplicate service env projection for target {target!r}")
        projected_targets.add(target)
        required = _string_list(raw.get("required_source_keys"), label=f"{label}.required_source_keys", violations=violations)
        runtime = _string_list(raw.get("runtime_keys"), label=f"{label}.runtime_keys", violations=violations)
        omitted_required = set(required) - set(runtime)
        if omitted_required:
            violations.append(
                f"env_contract.yaml: {label}.required_source_keys missing from runtime_keys: "
                + ", ".join(sorted(omitted_required))
            )
        if target_cfg.get("internal_service_token_required") is False and "INTERNAL_SERVICE_TOKEN" in runtime:
            violations.append(
                f"env_contract.yaml: {label} injects INTERNAL_SERVICE_TOKEN although target {target!r} has no token consumer"
            )
    return violations


def validate_auth_example_profiles(root: Path, contract: dict[str, Any]) -> list[str]:
    """Validate the auth profile values carried by env examples.

    This belongs here rather than in a second CLI: both checks read the same
    template files and the env contract already owns each template's role.
    """
    violations: list[str] = []
    examples = contract.get("auth_example_profiles")
    if not isinstance(examples, dict) or not examples:
        return [
            "env_contract.yaml: auth_example_profiles must be a non-empty mapping"
        ]
    for filename, profile in examples.items():
        if not isinstance(filename, str) or not isinstance(profile, str) or not profile:
            violations.append("env_contract.yaml: auth_example_profiles entries must map filename to profile name")
            continue
        path = root / filename
        if not path.exists():
            violations.append(f"{filename}: file not found for auth profile validation")
            continue
        try:
            values = parse_env_file(path).values
        except RuntimeError as exc:
            violations.append(f"{filename}: invalid env syntax: {exc}")
            continue
        expected = auth_profile_env_values(profile)
        mismatches = {
            key: (values.get(key), expected_value)
            for key, expected_value in expected.items()
            if values.get(key) != expected_value
        }
        if mismatches:
            violations.append(f"{filename}: auth profile {profile!r} mismatch: {mismatches}")
        app_env = values.get("APP_ENV", "").lower()
        if app_env not in {"local", "test", "development"} and values.get("API_KEY_REQUIRED") != "true":
            violations.append(
                f"{filename}: non-local APP_ENV={values.get('APP_ENV', '')!r} requires API_KEY_REQUIRED=true"
            )
    return violations


def validate(root: Path = ROOT, strict: bool = False) -> list[str]:
    violations: list[str] = []

    contract_path = root / "configs" / "env_contract.yaml"
    if not contract_path.exists():
        violations.append(f"configs/env_contract.yaml not found at {contract_path}")
        return violations

    contract = load_yaml(contract_path)
    violations.extend(validate_contract_structure(contract))
    env_examples = contract.get("env_examples")
    if not isinstance(env_examples, dict):
        env_examples = {}
    removed_keys = contract.get("removed_keys")
    if removed_keys is None:
        removed_keys = {}
    elif not isinstance(removed_keys, dict):
        violations.append("env_contract.yaml: removed_keys must be a mapping")
        removed_keys = {}
    violations.extend(validate_service_env_projections(root, contract))
    violations.extend(validate_auth_example_profiles(root, contract))
    main_profiles = load_yaml(
        root / "configs" / "main_model_profiles.yaml"
    ).get("profiles", {})
    services = load_yaml(root / "configs" / "services.yaml").get("services", {})
    expected_gateway_port = str(services.get("gateway", {}).get("default_host_port", ""))

    for filename, cfg in env_examples.items():
        file_path = root / filename
        if not file_path.exists():
            violations.append(f"{filename}: file not found")
            continue

        parse_result = parse_env_file(file_path)
        violations.extend(parse_result.errors)
        values = parse_result.values
        present_keys = set(values)
        if not isinstance(cfg, dict):
            continue  # validate_contract_structure()가 보고한다.
        key_set_names = cfg.get("required_key_sets")
        if not isinstance(key_set_names, list):
            continue  # validate_contract_structure()가 보고한다.
        required_keys = list(
            dict.fromkeys(
                expand_required_keys(
                    contract,
                    key_set_names,
                    violations=violations,
                )
            )
        )

        for key in required_keys:
            if key not in present_keys:
                violations.append(f"{filename}: missing required key {key!r}")

        # removed_keys는 sync-env가 기존 .env에서 지우는 키다. 그 키가 예시 파일에
        # 다시 들어오면 두 동작이 정면으로 싸운다 -- 템플릿은 "이 키를 쓰라"고 하고
        # sync-env는 매번 지운다. 등록과 템플릿이 갈라지는 걸 여기서 막는다.
        for key in sorted(removed_keys.keys() & present_keys):
            violations.append(
                f"{filename}: {key!r} is registered in env_contract.yaml removed_keys "
                f"(`make sync-env` deletes it), so it must not be declared in the template "
                f"-- {removed_keys[key]}"
            )

        static_profile = values.get("MAIN_LLM_STATIC_PROFILE", "").strip()
        if "MAIN_LLM_STATIC_PROFILE" in values and static_profile not in main_profiles:
            violations.append(
                f"{filename}: MAIN_LLM_STATIC_PROFILE references unknown profile "
                f"{static_profile!r}"
            )

        gateway_port = values.get("GATEWAY_PORT", "").strip()
        if "GATEWAY_PORT" in values and gateway_port != expected_gateway_port:
            violations.append(
                f"{filename}: GATEWAY_PORT={gateway_port} does not match "
                "configs/services.yaml gateway.default_host_port="
                f"{expected_gateway_port}"
            )

    if strict:
        # exposure profile마다 필요한 env key 묶음이 빠지지 않았는지 확인한다.
        # 예시 파일의 실제 EXPOSURE_MODE 값 유효성은 compose preflight가 소유한다.
        exposure_path = root / "configs" / "exposure_profiles.yaml"
        if not exposure_path.exists():
            violations.append("configs/exposure_profiles.yaml not found — cannot verify EXPOSURE_MODE values")
        else:
            exposure_data = load_yaml(exposure_path)
            profiles = exposure_data.get("profiles", {})
            # base가 아닌 profile마다 exposure_mode_requirements에 항목이 있는지 확인
            mode_reqs: dict = contract.get("exposure_mode_requirements", {})
            non_base_modes = [
                m for m, profile in profiles.items()
                if isinstance(profile, dict) and profile.get("class") != "default_private"
            ]
            for mode in non_base_modes:
                if mode not in mode_reqs:
                    violations.append(
                        f"env_contract.yaml: exposure_mode_requirements missing entry for non-base profile {mode!r}"
                    )

    return violations


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Validate .env examples against configs/env_contract.yaml.")
    parser.add_argument("--strict", action="store_true", help="EXPOSURE_MODE 값이 exposure_profiles.yaml과 일치하는지도 검증합니다")
    args = parser.parse_args()

    violations = validate(ROOT, strict=args.strict)

    if violations:
        for v in violations:
            print(f"FAIL: {v}", file=sys.stderr)
        print(f"\nvalidate_env_contract: {len(violations)} violation(s) found.", file=sys.stderr)
        return 1

    print("validate_env_contract: OK — .env examples match env_contract.yaml")
    if args.strict:
        print("  (strict mode: exposure profile requirement coverage verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
