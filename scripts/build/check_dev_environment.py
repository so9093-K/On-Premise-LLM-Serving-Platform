"""Check developer tools without loading application settings or credentials."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check_bash() -> None:
    bash = shutil.which("bash")
    if bash is None:
        raise RuntimeError("Bash >=4 is required; install Bash and add it to PATH.")
    result = subprocess.run(
        [bash, "-c", 'printf "%s" "$BASH_VERSION"; test "${BASH_VERSINFO[0]}" -ge 4'],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Bash >=4 is required by the existing shell helpers; found {bash} "
            f"({result.stdout.strip() or 'unknown version'}). On macOS: brew install bash, "
            'then export PATH="$(brew --prefix bash)/bin:$PATH". '
            "On Ubuntu: install bash and check PATH."
        )
    print(f"[dev] bash {result.stdout.strip()} ({bash})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="development")
    args = parser.parse_args()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build/check_python.py"), "--context", args.context],
        check=False,
    )
    if result.returncode:
        return result.returncode
    try:
        check_bash()
    except (OSError, RuntimeError) as exc:
        print(f"[dev] {exc}", file=sys.stderr)
        return 2
    recommended = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    print(f"[dev] python {sys.executable}; reference version: {recommended}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
