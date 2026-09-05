"""Developer bootstrap preserves existing state and fails before unsupported tools run."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.build import check_dev_environment, setup_dev


@pytest.mark.parametrize("version,returncode", [("3.2.57", 1), ("5.3.15", 0)])
def test_bash_check_reports_unsupported_runtime(monkeypatch, version, returncode):
    monkeypatch.setattr(check_dev_environment.shutil, "which", lambda _: "/example/bash")
    monkeypatch.setattr(
        check_dev_environment.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], returncode, version, ""),
    )
    if returncode:
        with pytest.raises(RuntimeError, match="Bash >=4"):
            check_dev_environment.check_bash()
    else:
        check_dev_environment.check_bash()


def test_setup_refuses_unknown_existing_directory_without_removing_it(tmp_path):
    existing = tmp_path / ".venv"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("existing files", encoding="utf-8")
    with pytest.raises(RuntimeError, match="inspect it"):
        setup_dev.prepare_venv(tmp_path)
    assert marker.read_text() == "existing files"


def test_setup_reuses_venv_without_recreating_it(tmp_path, monkeypatch):
    directory = tmp_path / ".venv"
    (directory / "bin").mkdir(parents=True)
    (directory / "bin/python").touch()
    (directory / "pyvenv.cfg").write_text("existing", encoding="utf-8")
    marker = directory / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    original_config = (directory / "pyvenv.cfg").read_bytes()
    monkeypatch.setattr(setup_dev, "_interpreter_minor", lambda _: setup_dev._running_minor())

    def reject_creation(*args, **kwargs):
        raise AssertionError("existing .venv must not be recreated")

    monkeypatch.setattr(setup_dev.subprocess, "run", reject_creation)

    python = setup_dev.prepare_venv(tmp_path)

    assert python == directory / "bin/python"
    assert (directory / "pyvenv.cfg").read_bytes() == original_config
    assert marker.read_text() == "keep"


def test_setup_refuses_different_existing_python_minor(tmp_path, monkeypatch):
    directory = tmp_path / ".venv"
    (directory / "bin").mkdir(parents=True)
    (directory / "bin/python").touch()
    (directory / "pyvenv.cfg").write_text("existing", encoding="utf-8")
    monkeypatch.setattr(setup_dev, "_interpreter_minor", lambda _: "3.8")
    with pytest.raises(RuntimeError, match="Existing .venv uses Python 3.8"):
        setup_dev.prepare_venv(tmp_path)
    assert (directory / "pyvenv.cfg").read_text() == "existing"


def test_setup_refuses_base_python_that_would_create_a_different_minor(tmp_path, monkeypatch):
    selected = setup_dev._running_minor()
    different = "3.10" if selected != "3.10" else "3.11"
    creator = Path("/system/python3")
    monkeypatch.setattr(setup_dev, "_venv_creator", lambda: creator)
    monkeypatch.setattr(setup_dev, "_interpreter_minor", lambda _: different)

    def reject_creation(*args, **kwargs):
        raise AssertionError("mismatched base interpreter must not create .venv")

    monkeypatch.setattr(setup_dev.subprocess, "run", reject_creation)

    with pytest.raises(RuntimeError, match=rf"base interpreter .* uses Python {different}"):
        setup_dev.prepare_venv(tmp_path)
    assert not (tmp_path / ".venv").exists()


def test_setup_creates_venv_with_the_explicit_base_interpreter(tmp_path, monkeypatch):
    selected = setup_dev._running_minor()
    creator = Path("/opt/python/bin/python3.12")
    target = tmp_path / ".venv/bin/python"
    monkeypatch.setattr(setup_dev, "_venv_creator", lambda: creator)
    monkeypatch.setattr(setup_dev, "_interpreter_minor", lambda _: selected)

    def create(command, *, check, **kwargs):
        assert command == [str(creator), "-m", "venv", str(tmp_path / ".venv")]
        assert check is True
        target.parent.mkdir(parents=True)
        target.touch()
        (tmp_path / ".venv/pyvenv.cfg").write_text("created", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(setup_dev.subprocess, "run", create)

    assert setup_dev.prepare_venv(tmp_path) == target


def test_setup_stops_before_creating_venv_when_python_is_unsupported(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_dev, "ROOT", tmp_path)

    def fail_python_check(*args, **kwargs):
        raise subprocess.CalledProcessError(2, args[0])

    monkeypatch.setattr(setup_dev.subprocess, "run", fail_python_check)
    assert setup_dev.main() == 2
    assert not (tmp_path / ".venv").exists()


def test_setup_checks_python_without_requiring_operational_bash(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools==83.0.0"]\n', encoding="utf-8",
    )
    monkeypatch.setattr(setup_dev, "ROOT", tmp_path)
    monkeypatch.setattr(setup_dev, "prepare_venv", lambda _: tmp_path / ".venv/bin/python")
    commands = []

    def record(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(setup_dev.subprocess, "run", record)

    assert setup_dev.main() == 0
    assert commands[0][1].endswith("scripts/build/check_python.py")
    assert not any(
        "check_dev_environment.py" in str(part)
        for command in commands
        for part in command
    )
