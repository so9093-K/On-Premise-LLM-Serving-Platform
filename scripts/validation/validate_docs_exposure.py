#!/usr/bin/env python3
"""Validate exposure docs keep structured diagnostics terminology.

Checks:
- exposure config declares canonical modes to anchor the docs gate
- retired field name 'expected_side_effects' does not reappear in docs

Exclusions:
- docs/adr/: ADRs are historical decision records

Usage:
  python scripts/validation/validate_docs_exposure.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

try:
    import yaml
except ModuleNotFoundError:
    raise SystemExit("Missing dependency: PyYAML.")

def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def find_docs(root: Path) -> list[Path]:
    docs_dir = root / "docs"
    if not docs_dir.exists():
        return []
    adr_dir = docs_dir / "adr"
    return sorted(
        p for p in docs_dir.rglob("*.md")
        # ADRs are historical decision records — they intentionally reference past names and fields
        if not p.is_relative_to(adr_dir)
    )


def validate(root: Path = ROOT) -> list[str]:
    violations: list[str] = []

    exposure_path = root / "configs" / "exposure_profiles.yaml"
    data = load_yaml(exposure_path)

    canonical_modes: list[str] = data.get("canonical_modes", [])
    if not canonical_modes:
        violations.append("configs/exposure_profiles.yaml: canonical_modes is empty — nothing to validate against")
        return violations

    retired_fields = {"expected_side_effects"}

    docs = find_docs(root)
    if not docs:
        violations.append("docs/ directory is empty or not found")
        return violations

    for doc_path in docs:
        rel = doc_path.relative_to(root)
        text = doc_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        for lineno, line in enumerate(lines, start=1):
            for field in retired_fields:
                if field in line and "deprecated" not in line.lower() and not line.strip().startswith("#"):
                    violations.append(
                        f"{rel}:{lineno}: retired field {field!r} found in docs — use structured diagnostics instead"
                    )

    return violations


def main() -> int:
    violations = validate(ROOT)

    if violations:
        for v in violations:
            print(f"FAIL: {v}", file=sys.stderr)
        print(f"\nvalidate_docs_exposure: {len(violations)} violation(s) found.", file=sys.stderr)
        return 1

    print("validate_docs_exposure: OK — no stale canonical exposure references in docs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
