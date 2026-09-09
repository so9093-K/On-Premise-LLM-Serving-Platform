"""Only repository-owned bootstrap safety policy is tested here; uv owns sync behavior."""
from __future__ import annotations

import subprocess

import pytest

from scripts.build import check_dev_environment, setup_dev


@pytest.mark.parametrize("version,returncode", [("3.2.57", 1), ("5.3.15", 0)])
def test_bash_check_reports_unsupported_runtime(monkeypatch, version, returncode):
    monkeypatch.setattr(check_dev_environment.shutil, "which", lambda _: "/example/bash")
    monkeypatch.setattr(
        check_dev_environment.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], returncode, version, ""),
    )
    if returncode:
        with pytest.raises(RuntimeError, match="Bash >=4"):
            check_dev_environment.check_bash()
    else:
        check_dev_environment.check_bash()


def test_setup_preserves_unknown_existing_environment(tmp_path):
    existing = tmp_path / ".venv"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("existing files", encoding="utf-8")

    with pytest.raises(RuntimeError, match="inspect it"):
        setup_dev._check_existing_environment(tmp_path, tmp_path / "python")

    assert marker.read_text(encoding="utf-8") == "existing files"


def test_setup_refuses_different_existing_python_minor(tmp_path, monkeypatch):
    directory = tmp_path / ".venv"
    (directory / "bin").mkdir(parents=True)
    (directory / "bin/python").touch()
    (directory / "pyvenv.cfg").write_text("existing", encoding="utf-8")
    versions = iter(("3.12", "3.13"))
    monkeypatch.setattr(setup_dev, "_interpreter_minor", lambda _: next(versions))

    with pytest.raises(RuntimeError, match="Existing .venv uses Python 3.12"):
        setup_dev._check_existing_environment(tmp_path, tmp_path / "python3.13")

    assert (directory / "pyvenv.cfg").read_text(encoding="utf-8") == "existing"


def test_setup_checks_python_before_environment_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_dev, "ROOT", tmp_path)

    def fail_python_check(*args, **kwargs):
        raise subprocess.CalledProcessError(2, args[0])

    monkeypatch.setattr(setup_dev.subprocess, "run", fail_python_check)
    assert setup_dev.main() == 2
    assert not (tmp_path / ".venv").exists()
