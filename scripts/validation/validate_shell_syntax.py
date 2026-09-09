#!/usr/bin/env python3
"""scripts/**/*.sh와 ops/**/*.sh를 `bash -n`으로 구문 검사한다."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _bash_n(label: str, script_text: str) -> str | None:
    result = subprocess.run(
        ["bash", "-n"],
        input=script_text,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"{label}: {result.stderr.strip()}"
    return None


def validate(root: Path) -> list[str]:
    violations: list[str] = []
    sh_files = sorted({*root.glob("scripts/**/*.sh"), *root.glob("ops/**/*.sh")})
    for path in sh_files:
        rel = path.relative_to(root)
        error = _bash_n(str(rel), path.read_text(encoding="utf-8"))
        if error:
            violations.append(error)

    return violations


def main() -> int:
    violations = validate(ROOT)

    if violations:
        for v in violations:
            print(f"FAIL: {v}", file=sys.stderr)
        print(f"\nvalidate_shell_syntax: {len(violations)} violation(s) found.", file=sys.stderr)
        return 1

    print("validate_shell_syntax: OK — all shell scripts parse cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
