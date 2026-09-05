"""Prepare the app/contract environment, preserving env files and runtime state."""
from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def prepare_venv(root: Path) -> Path:
    directory = root / ".venv"
    python = directory / "bin/python"
    if directory.exists():
        if not (directory / "pyvenv.cfg").is_file() or not python.is_file():
            raise RuntimeError("Existing .venv is not a usable virtual environment; inspect it before retrying.")
        version = subprocess.check_output(
            [str(python), "-c", 'import sys; print("%s.%s" % sys.version_info[:2])'],
            text=True,
        ).strip()
        requested = f"{sys.version_info.major}.{sys.version_info.minor}"
        if version != requested:
            raise RuntimeError(
                f"Existing .venv uses Python {version}, selected interpreter uses {requested}. "
                "Reuse it with PYTHON_BIN=.venv/bin/python, or move it aside before creating a new environment."
            )
    else:
        venv.EnvBuilder(with_pip=True).create(directory)
    return python


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
        # Python policy is checked before importing tomllib, so old interpreters
        # receive the same actionable diagnostic as make validate.
        import tomllib

        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        python = prepare_venv(ROOT)
        pip = [str(python), "-m", "pip", "--disable-pip-version-check"]
        subprocess.run(
            [*pip, "install", "--no-deps", "-r", str(ROOT / "requirements.lock")], check=True,
        )
        subprocess.run([*pip, "install", *metadata["build-system"]["requires"]], check=True)
        subprocess.run(
            [*pip, "install", "--no-deps", "--no-build-isolation", "-e", ".[contract]"],
            cwd=ROOT, check=True,
        )
        subprocess.run([*pip, "check"], check=True)
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except (OSError, RuntimeError) as exc:
        print(f"[setup-dev] {exc}", file=sys.stderr)
        return 2
    print("[setup-dev] ready: make validate && make test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
