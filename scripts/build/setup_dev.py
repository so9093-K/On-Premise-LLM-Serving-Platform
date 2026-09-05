"""Prepare the app/contract environment, preserving env files and runtime state."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _running_minor() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _interpreter_minor(executable: Path) -> str:
    return subprocess.check_output(
        [str(executable), "-c", 'import sys; print("%s.%s" % sys.version_info[:2])'],
        text=True,
    ).strip()


def _venv_creator() -> Path:
    """Return the base interpreter that Python's venv machinery would use."""
    return Path(getattr(sys, "_base_executable", None) or sys.executable)


def prepare_venv(root: Path) -> Path:
    directory = root / ".venv"
    python = directory / "bin/python"
    selected = _running_minor()
    if directory.exists():
        if not (directory / "pyvenv.cfg").is_file() or not python.is_file():
            raise RuntimeError("Existing .venv is not a usable virtual environment; inspect it before retrying.")
        version = _interpreter_minor(python)
        if version != selected:
            raise RuntimeError(
                f"Existing .venv uses Python {version}, selected interpreter uses {selected}. "
                "Reuse it with PYTHON_BIN=.venv/bin/python, or move it aside before creating a new environment."
            )
    else:
        # EnvBuilder and `python -m venv` create from sys._base_executable when
        # setup-dev itself is running inside another venv. Make that implicit
        # choice explicit and refuse to create a target with a different minor.
        creator = _venv_creator()
        creator_version = _interpreter_minor(creator)
        if creator_version != selected:
            raise RuntimeError(
                f"Selected interpreter uses Python {selected}, but its venv base interpreter "
                f"{creator} uses Python {creator_version}. Run setup-dev with a base Python "
                f"{selected} executable so the new .venv has the selected minor."
            )
        subprocess.run([str(creator), "-m", "venv", str(directory)], check=True)
        if not (directory / "pyvenv.cfg").is_file() or not python.is_file():
            raise RuntimeError("Python venv creation completed without a usable .venv; inspect it before retrying.")
        created_version = _interpreter_minor(python)
        if created_version != selected:
            raise RuntimeError(
                f"New .venv uses Python {created_version}, selected interpreter uses {selected}; "
                "the environment was preserved for inspection."
            )
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
