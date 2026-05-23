#!/usr/bin/env python3
"""Validate that .env example files contain all keys declared in configs/env_contract.yaml.

Checks:
- .env.example, .env.local.example, .env.compose.example each contain their required key sets
- Required key sets: always_required, auth_mode_keys, model_runtime prefixed keys, exposure keys
- EXPOSURE_MODE allowed values comment matches canonical_modes from exposure_profiles.yaml

Usage:
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
    """Return the set of keys defined (key=...) in an env example file."""
    return set(parse_env_file(path).values)


def expand_required_keys(contract: dict[str, Any], key_set_names: list[str]) -> list[str]:
    """Expand a list of key_set references from env_contract.yaml into concrete key names."""
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
            # Direct list from contract top level
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
        required_keys = list(dict.fromkeys(expand_required_keys(contract, key_set_names)))  # dedup, order-preserving

        for key in required_keys:
            if key not in present_keys:
                violations.append(f"{filename}: missing required key {key!r}")

    if strict:
        # Verify EXPOSURE_MODE canonical values match exposure_profiles.yaml
        exposure_path = root / "configs" / "exposure_profiles.yaml"
        if not exposure_path.exists():
            violations.append("configs/exposure_profiles.yaml not found — cannot verify EXPOSURE_MODE values")
        else:
            exposure_data = load_yaml(exposure_path)
            canonical_modes: list[str] = exposure_data.get("canonical_modes", [])
            # Check that canonical_modes appear in exposure_mode_requirements
            req_any = contract.get("exposure_mode_requirements", {}).get("any", [])
            if "EXPOSURE_MODE" not in req_any:
                violations.append("env_contract.yaml: exposure_mode_requirements.any does not include EXPOSURE_MODE")

            # Check that exposure_mode_requirements has an entry for each non-base canonical mode
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
    parser.add_argument("--strict", action="store_true", help="Also verify EXPOSURE_MODE values match exposure_profiles.yaml")
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
