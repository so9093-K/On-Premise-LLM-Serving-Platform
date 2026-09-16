from __future__ import annotations

import runpy
from pathlib import Path

from scripts.build import setup_python_environment
from scripts.build.setup_python_environment import build_sync_command


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_profile_excludes_quality_dependencies() -> None:
    command = build_sync_command("uv", Path("/python"), "runtime")

    assert command == [
        "uv",
        "sync",
        "--locked",
        "--no-group",
        "quality",
        "--python",
        "/python",
    ]


def test_development_profile_includes_quality_dependencies() -> None:
    command = build_sync_command("uv", Path("/python"), "development")

    assert command == [
        "uv",
        "sync",
        "--locked",
        "--group",
        "quality",
        "--python",
        "/python",
    ]


def test_make_setup_uses_runtime_profile_and_setup_dev_keeps_dev_entrypoint() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert (
        '"$(PYTHON)" scripts/build/setup_python_environment.py --profile runtime'
        in makefile
    )
    assert '"$(PYTHON)" scripts/build/setup_dev.py' in makefile


def test_setup_dev_wrapper_selects_development_profile(monkeypatch) -> None:
    captured: list[list[str]] = []

    def fake_main(argv: list[str] | None = None) -> int:
        captured.append(list(argv or []))
        return 0

    monkeypatch.setattr(setup_python_environment, "main", fake_main)

    try:
        runpy.run_path(str(ROOT / "scripts/build/setup_dev.py"), run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0

    assert captured == [["--profile", "development"]]
