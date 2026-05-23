from __future__ import annotations

import subprocess
import shlex
from pathlib import Path

import pytest

from ai_model_serving.settings_parts.dotenv_parser import load_strict_env_file, parse_env_file

ROOT = Path(__file__).resolve().parents[2]


def test_strict_dotenv_rejects_duplicate_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AUTH_MODE=local_open\nAUTH_MODE=strict\n", encoding="utf-8")

    result = parse_env_file(env_file)

    assert any("duplicate env key 'AUTH_MODE'" in error for error in result.errors)
    with pytest.raises(RuntimeError, match="duplicate env key 'AUTH_MODE'"):
        load_strict_env_file(env_file)


def test_strict_dotenv_rejects_quoted_control_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('EXPOSURE_MODE="master_open"\n', encoding="utf-8")

    result = parse_env_file(env_file)

    assert any("quoted values are not supported" in error for error in result.errors)


def test_strict_dotenv_rejects_colon_and_spaces(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("EXPOSURE_MODE: master_open\nAUTH_MODE =strict\nAPP_ENV= production\n", encoding="utf-8")

    result = parse_env_file(env_file)

    assert any("expected KEY=VALUE" in error for error in result.errors)
    assert any("spaces around KEY=VALUE" in error for error in result.errors)


def test_strict_dotenv_accepts_plain_key_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("EXPOSURE_MODE=master_open\nEXPOSURE_AUDIENCE=private_lan\n", encoding="utf-8")

    assert load_strict_env_file(env_file) == {
        "EXPOSURE_MODE": "master_open",
        "EXPOSURE_AUDIENCE": "private_lan",
    }


def test_shell_load_env_reports_invalid_key_without_bash_export_error(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("BAD-KEY=1\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f"source scripts/lib/load_env.sh && load_local_env {shlex.quote(str(env_file))}",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "[load-env] invalid env key" in result.stderr
    assert "BAD-KEY" in result.stderr
    assert "invalid variable name" not in result.stderr
