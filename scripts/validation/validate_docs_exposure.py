#!/usr/bin/env python3
"""Validate that docs and feature manifests do not reference deprecated exposure aliases as canonical.

Reads configs/exposure_profiles.yaml for canonical_modes and deprecated_aliases.
Scans docs/**/*.md and features/*.yaml for patterns that would indicate stale canonical references.

Checks:
- deprecated alias names do not appear in canonical mode tables (| alias |) in Markdown
- retired field name 'expected_side_effects' does not appear in docs
- docs do not list deprecated aliases alongside canonical modes in table rows
- features/*.yaml do not contain deprecated alias names outside of explicitly deprecated sections

The intent is NOT to forbid all mention of deprecated aliases (mentioning
them in a "deprecated" section is fine), but to detect cases where they appear
as if they were still canonical.

Exclusions:
- docs/adr/: ADRs are historical decision records and intentionally reference past names/fields
- configs/exposure_profiles.yaml itself: the source of truth that defines deprecated_aliases

Usage:
  python scripts/validation/validate_docs_exposure.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

try:
    import yaml
except ModuleNotFoundError:
    raise SystemExit("Missing dependency: PyYAML.")

# Markdown table row pattern: | ... | ... |
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|")


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


def find_feature_manifests(root: Path) -> list[Path]:
    """Return YAML files in features/ that should not reference deprecated aliases as canonical."""
    features_dir = root / "features"
    if not features_dir.exists():
        return []
    return sorted(features_dir.glob("*.yaml"))


def validate(root: Path = ROOT) -> list[str]:
    violations: list[str] = []

    exposure_path = root / "configs" / "exposure_profiles.yaml"
    data = load_yaml(exposure_path)

    canonical_modes: list[str] = data.get("canonical_modes", [])
    deprecated_aliases: dict = data.get("deprecated_aliases", {})

    if not canonical_modes:
        violations.append("configs/exposure_profiles.yaml: canonical_modes is empty — nothing to validate against")
        return violations

    alias_names = set(deprecated_aliases.keys())
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
            # Check 1: deprecated alias appearing inside a table row
            # We flag: | `alias` | or | alias | patterns in table rows
            # but NOT lines that contain the word "deprecated" — those are intentional
            if _TABLE_ROW_RE.match(line) and "deprecated" not in line.lower():
                for alias in alias_names:
                    # Match alias as a standalone word or backtick-quoted in a table cell
                    if re.search(rf"[`\s|]{re.escape(alias)}[`\s|]", line):
                        violations.append(
                            f"{rel}:{lineno}: deprecated alias {alias!r} appears in a table row without 'deprecated' context"
                        )

            # Check 2: retired field name in docs
            for field in retired_fields:
                if field in line and "deprecated" not in line.lower() and not line.strip().startswith("#"):
                    violations.append(
                        f"{rel}:{lineno}: retired field {field!r} found in docs — use structured diagnostics instead"
                    )

    # Check feature manifests (features/*.yaml) for stale deprecated alias references.
    # Uses indentation-based context tracking to skip lines inside `deprecated_*:` YAML blocks.
    feature_manifests = find_feature_manifests(root)
    for manifest_path in feature_manifests:
        rel = manifest_path.relative_to(root)
        lines = manifest_path.read_text(encoding="utf-8").splitlines()

        in_deprecated_block = False
        deprecated_block_indent = -1

        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # Track YAML key context to detect `deprecated_*:` blocks
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

            for alias in alias_names:
                if alias in line and "deprecated" not in line.lower():
                    violations.append(
                        f"{rel}:{lineno}: deprecated alias {alias!r} appears outside a deprecated section"
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
