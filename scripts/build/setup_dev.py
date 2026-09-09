"""Synchronize the application development environment from the project lock."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _interpreter_minor(executable: Path) -> str:
    return subprocess.check_output(
        [str(executable), "-c", 'import sys; print("%s.%s" % sys.version_info[:2])'],
        text=True,
    ).strip()


def _uv_binary() -> str:
    configured = os.environ.get("UV_BIN")
    executable = configured or shutil.which("uv")
    if not executable:
        raise RuntimeError(
            "uv is required to prepare Python environments; install the version "
            "declared by pyproject.toml "
            "or set UV_BIN=/path/to/uv"
        )
    return executable


def _check_existing_environment(root: Path, selected: Path) -> None:
    directory = root / ".venv"
    if not directory.exists():
        return
    python = directory / "bin/python"
    if not (directory / "pyvenv.cfg").is_file() or not python.is_file():
        raise RuntimeError(
            "Existing .venv is not a usable virtual environment; inspect it before retrying."
        )
    current_minor = _interpreter_minor(python)
    selected_minor = _interpreter_minor(selected)
    if current_minor != selected_minor:
        raise RuntimeError(
            f"Existing .venv uses Python {current_minor}, selected interpreter uses "
            f"{selected_minor}. Reuse it with PYTHON_BIN=.venv/bin/python, or move it "
            "aside before creating a new environment."
        )


def main() -> int:
    try:
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/build/check_python.py"),
                "--context",
                "setup-dev",
            ],
            check=True,
        )
        selected = Path(sys.executable).resolve()
        _check_existing_environment(ROOT, selected)
        subprocess.run(
            [
                _uv_binary(),
                "sync",
                "--locked",
                "--group",
                "quality",
                "--python",
                str(selected),
            ],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except (OSError, RuntimeError) as exc:
        print(f"[setup-dev] {exc}", file=sys.stderr)
        return 2
    print("[setup-dev] ready: make check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
