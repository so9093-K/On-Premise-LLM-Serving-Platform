#!/usr/bin/env python3
"""Validate configs/exposure_profiles.yaml structural invariants.

Checks:
- canonical_modes field exists and is non-empty
- profiles.keys() exactly matches canonical_modes
- deprecated_aliases names do not overlap with profiles
- deprecated_aliases have required fields: target, reason, remove_after
- deprecated_aliases targets exist in canonical_modes
- each profile has class, diagnostics, host_published
- exactly one profile has class: default_private
- exactly one profile has class: diagnostic_full_stack
- default_private profile does not expose internal/diagnostic services
- diagnostic_full_stack profile exposes all expected service categories
- services section covers all services referenced in profiles.host_published
- compose override file exists for each canonical non-base mode

Usage:
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

# Services that must NOT appear in default_private host_published
_PRIVATE_EXCLUDED = frozenset({
    "main_llm_vllm", "embedding_vllm", "embedding_ko_vllm", "risk_prompt_vllm",
    "risk_adapter", "prometheus", "dcgm_exporter", "cadvisor",
})

# Service categories that diagnostic_full_stack must include
_DIAGNOSTIC_REQUIRED_CATEGORIES = {
    "vllm_runtimes": frozenset({"main_llm_vllm", "embedding_vllm", "embedding_ko_vllm", "risk_prompt_vllm"}),
    "risk_adapter": frozenset({"risk_adapter"}),
    "operations_metrics": frozenset({"prometheus", "dcgm_exporter", "cadvisor"}),
    "visualization": frozenset({"grafana"}),
}

# Required fields per profile
_PROFILE_REQUIRED_FIELDS = ("class", "diagnostics", "host_published", "description")

# Required diagnostics boolean fields
_DIAGNOSTICS_FIELDS = (
    "gateway_bypass_possible",
    "direct_model_runtime_access",
    "direct_risk_adapter_access",
    "direct_operations_endpoints",
    "requires_exposure_audience",
)

# Required fields per deprecated alias
_ALIAS_REQUIRED_FIELDS = ("target", "reason", "remove_after")


def load(path: Path) -> dict:
    if not path.exists():
        print(f"FAIL: configs/exposure_profiles.yaml not found at {path}", file=sys.stderr)
        raise SystemExit(1)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print("FAIL: configs/exposure_profiles.yaml is not a YAML mapping", file=sys.stderr)
        raise SystemExit(1)
    return data


def validate(data: dict, strict: bool = False) -> list[str]:
    """Return list of violation messages. Empty → valid."""
    violations: list[str] = []

    # 1. canonical_modes field
    canonical_modes: list[str] = data.get("canonical_modes", [])
    if not canonical_modes:
        violations.append("canonical_modes field is missing or empty")
        return violations  # cannot continue meaningfully

    # 2. profiles.keys() must exactly match canonical_modes
    profiles: dict = data.get("profiles", {})
    profile_names = set(profiles.keys())
    canonical_set = set(canonical_modes)
    if profile_names != canonical_set:
        extra = profile_names - canonical_set
        missing = canonical_set - profile_names
        if extra:
            violations.append(f"profiles has keys not in canonical_modes: {sorted(extra)}")
        if missing:
            violations.append(f"canonical_modes has modes not in profiles: {sorted(missing)}")

    # 3. deprecated_aliases must not overlap with profiles
    aliases: dict = data.get("deprecated_aliases", {})
    overlap = set(aliases.keys()) & profile_names
    if overlap:
        violations.append(f"deprecated_aliases names overlap with profiles: {sorted(overlap)}")

    # 4. deprecated_aliases required fields
    for alias_name, alias in aliases.items():
        if not isinstance(alias, dict):
            violations.append(f"deprecated_aliases.{alias_name} is not a mapping")
            continue
        for field in _ALIAS_REQUIRED_FIELDS:
            if field not in alias:
                violations.append(f"deprecated_aliases.{alias_name} missing required field: {field!r}")
        target = alias.get("target")
        if target and target not in canonical_set:
            violations.append(f"deprecated_aliases.{alias_name}.target={target!r} not in canonical_modes")

    # 5. Each profile must have required fields and valid class
    classes_found: dict[str, list[str]] = {}
    for mode, profile in profiles.items():
        if not isinstance(profile, dict):
            violations.append(f"profiles.{mode} is not a mapping")
            continue
        for field in _PROFILE_REQUIRED_FIELDS:
            if field not in profile:
                violations.append(f"profiles.{mode} missing required field: {field!r}")

        # diagnostics block completeness
        diag = profile.get("diagnostics", {})
        if not isinstance(diag, dict):
            violations.append(f"profiles.{mode}.diagnostics is not a mapping")
        else:
            for df in _DIAGNOSTICS_FIELDS:
                if df not in diag:
                    violations.append(f"profiles.{mode}.diagnostics missing field: {df!r}")

        # class field
        cls = profile.get("class", "")
        classes_found.setdefault(cls, []).append(mode)

    # 6. Exactly one default_private and one diagnostic_full_stack
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

    # 7. default_private must not expose internal/diagnostic services
    for mode in default_private_modes:
        profile = profiles.get(mode, {})
        published = set(profile.get("host_published", []))
        exposed_internal = published & _PRIVATE_EXCLUDED
        if exposed_internal:
            violations.append(
                f"profiles.{mode} (default_private) must not host-publish: {sorted(exposed_internal)}"
            )
        diag = profile.get("diagnostics", {})
        for dangerous in ("gateway_bypass_possible", "direct_model_runtime_access", "direct_risk_adapter_access", "direct_operations_endpoints"):
            if diag.get(dangerous):
                violations.append(
                    f"profiles.{mode} (default_private) has diagnostics.{dangerous}=true — not allowed for default_private class"
                )

    # 8. diagnostic_full_stack must expose all required service categories
    for mode in diagnostic_full_stack_modes:
        profile = profiles.get(mode, {})
        published = set(profile.get("host_published", []))
        for category, required_svcs in _DIAGNOSTIC_REQUIRED_CATEGORIES.items():
            if not required_svcs.issubset(published):
                missing_svcs = required_svcs - published
                violations.append(
                    f"profiles.{mode} (diagnostic_full_stack) missing {category} services: {sorted(missing_svcs)}"
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

    # 9. services section covers all referenced service names
    services: dict = data.get("services", {})
    for mode, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        for svc in profile.get("host_published", []):
            if svc not in services:
                violations.append(
                    f"profiles.{mode}.host_published references service {svc!r} not defined in services section"
                )

    # 10. compose override file exists for each canonical non-base mode (strict only)
    if strict:
        overrides_dir = ROOT / "ops" / "compose" / "overrides"
        base_mode = default_private_modes[0] if len(default_private_modes) == 1 else None

        # Services already host-published in the base topology (default_private profile)
        # do not need to appear in the override file.
        base_published = set(profiles.get(base_mode, {}).get("host_published", [])) if base_mode else set()

        for mode in canonical_modes:
            if mode == base_mode:
                continue
            slug = mode.replace("_", "-")
            override_path = overrides_dir / f"exposure.{slug}.yaml"
            if not override_path.exists():
                violations.append(
                    f"compose override file missing for canonical mode {mode!r}: {override_path}"
                )
            else:
                # Verify override references services that are added on top of the base topology.
                # Services already in base_published are handled by the base compose file.
                profile = profiles.get(mode, {})
                published = set(profile.get("host_published", []))
                added_by_override = published - base_published
                override_text = override_path.read_text(encoding="utf-8")
                for svc_name in added_by_override:
                    svc_info = services.get(svc_name, {})
                    compose_svc = svc_info.get("compose_service", svc_name.replace("_", "-"))
                    if compose_svc not in override_text:
                        violations.append(
                            f"compose override {override_path.name} missing service {compose_svc!r} "
                            f"(profiles.{mode}.host_published adds {svc_name!r} over base topology)"
                        )

    return violations


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Validate configs/exposure_profiles.yaml structural invariants.")
    parser.add_argument("--strict", action="store_true", help="Also check compose override file consistency")
    args = parser.parse_args()

    data = load(ROOT / "configs" / "exposure_profiles.yaml")
    violations = validate(data, strict=args.strict)

    if violations:
        for v in violations:
            print(f"FAIL: {v}", file=sys.stderr)
        print(f"\nvalidate_exposure_profiles: {len(violations)} violation(s) found.", file=sys.stderr)
        return 1

    print("validate_exposure_profiles: OK — configs/exposure_profiles.yaml is structurally valid.")
    if args.strict:
        print("  (strict mode: compose override files verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
