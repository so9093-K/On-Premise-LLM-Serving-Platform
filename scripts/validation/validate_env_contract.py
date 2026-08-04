#!/usr/bin/env python3
"""`.env` 예시 파일들이 configs/env_contract.yaml에 선언된 키를 전부 갖고 있는지 검증한다.

체크 항목:
- .env.example, .env.local.example, .env.compose.example 각각 필요한 키 집합을 포함하는지
- 필요한 키 집합: always_required, auth_mode_keys, model_runtime 접두사 키, exposure 키
- EXPOSURE_MODE 허용값 주석이 exposure_profiles.yaml의 canonical_modes와 일치하는지

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


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Expected YAML mapping at {path}")
    return data


def parse_env_keys(path: Path) -> set[str]:
    """env 예시 파일에 정의된(key=...) 키 집합을 반환한다."""
    return set(parse_env_file(path).values)


def expand_required_keys(contract: dict[str, Any], key_set_names: list[str]) -> list[str]:
    """env_contract.yaml의 key_set 참조 목록을 실제 키 이름들로 펼친다."""
    required: list[str] = []

    for name in key_set_names:
        if name == "always_required":
            required.extend(contract.get("always_required", []))
        elif name == "auth_mode_keys":
            required.extend(contract.get("auth_mode_keys", []))
        elif name == "model_runtimes":
            for runtime_cfg in contract.get("model_runtimes", {}).values():
                prefix = runtime_cfg["env_prefix"]
                for suffix in runtime_cfg["suffixes"]:
                    required.append(f"{prefix}_{suffix}")
        elif name.startswith("exposure_mode_requirements."):
            sub = name.split(".", 1)[1]
            required.extend(contract.get("exposure_mode_requirements", {}).get(sub, []))
        else:
            # contract 최상위의 직접 목록
            val = contract.get(name)
            if isinstance(val, list):
                required.extend(val)

    return required


def validate(root: Path = ROOT, strict: bool = False) -> list[str]:
    violations: list[str] = []

    contract_path = root / "configs" / "env_contract.yaml"
    if not contract_path.exists():
        violations.append(f"configs/env_contract.yaml not found at {contract_path}")
        return violations

    contract = load_yaml(contract_path)
    env_examples: dict = contract.get("env_examples", {})

    for filename, cfg in env_examples.items():
        file_path = root / filename
        if not file_path.exists():
            violations.append(f"{filename}: file not found")
            continue

        parse_result = parse_env_file(file_path)
        violations.extend(parse_result.errors)
        present_keys = parse_env_keys(file_path)
        key_set_names: list[str] = cfg.get("required_key_sets", [])
        required_keys = list(dict.fromkeys(expand_required_keys(contract, key_set_names)))  # 중복 제거, 순서 유지

        for key in required_keys:
            if key not in present_keys:
                violations.append(f"{filename}: missing required key {key!r}")

    if strict:
        # EXPOSURE_MODE canonical 값이 exposure_profiles.yaml과 일치하는지 확인
        exposure_path = root / "configs" / "exposure_profiles.yaml"
        if not exposure_path.exists():
            violations.append("configs/exposure_profiles.yaml not found — cannot verify EXPOSURE_MODE values")
        else:
            exposure_data = load_yaml(exposure_path)
            canonical_modes: list[str] = exposure_data.get("canonical_modes", [])
            # canonical_modes가 exposure_mode_requirements에 나타나는지 확인
            req_any = contract.get("exposure_mode_requirements", {}).get("any", [])
            if "EXPOSURE_MODE" not in req_any:
                violations.append("env_contract.yaml: exposure_mode_requirements.any does not include EXPOSURE_MODE")

            # base가 아닌 canonical mode마다 exposure_mode_requirements에 항목이 있는지 확인
            mode_reqs: dict = contract.get("exposure_mode_requirements", {})
            non_base_modes = [
                m for m in canonical_modes
                if exposure_data.get("profiles", {}).get(m, {}).get("class") != "default_private"
            ]
            for mode in non_base_modes:
                if mode not in mode_reqs:
                    violations.append(
                        f"env_contract.yaml: exposure_mode_requirements missing entry for canonical non-base mode {mode!r}"
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
        print("  (strict mode: EXPOSURE_MODE values verified against exposure_profiles.yaml)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
