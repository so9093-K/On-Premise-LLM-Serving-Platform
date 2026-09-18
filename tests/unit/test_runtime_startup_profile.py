from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = "scripts/lib/runtime_startup_profile.sh"


def run_normalize(*aliases: str, **environment: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in ("RUNTIME_STARTUP_PROFILE", "RUNTIME_PROFILE", "DEPLOY_RUNTIME_PROFILE"):
        env.pop(key, None)
    env |= environment
    quoted = " ".join(aliases)
    command = (
        f"source {HELPER}; "
        f"normalize_runtime_startup_profile {quoted} || exit $?; "
        'printf "%s" "$RUNTIME_STARTUP_PROFILE"'
    )
    return subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_canonical_value_is_preserved():
    result = run_normalize("RUNTIME_PROFILE", RUNTIME_STARTUP_PROFILE="retrieval_ready")

    assert result.returncode == 0
    assert result.stdout == "retrieval_ready"


def test_legacy_alias_is_promoted_to_canonical_value():
    result = run_normalize("RUNTIME_PROFILE", RUNTIME_PROFILE="main_only")

    assert result.returncode == 0
    assert result.stdout == "main_only"


def test_matching_legacy_and_canonical_values_are_allowed():
    result = run_normalize(
        "DEPLOY_RUNTIME_PROFILE",
        RUNTIME_STARTUP_PROFILE="main_only",
        DEPLOY_RUNTIME_PROFILE="main_only",
    )

    assert result.returncode == 0
    assert result.stdout == "main_only"


def test_conflicting_legacy_and_canonical_values_fail_closed():
    result = run_normalize(
        "DEPLOY_RUNTIME_PROFILE",
        RUNTIME_STARTUP_PROFILE="main_only",
        DEPLOY_RUNTIME_PROFILE="retrieval_ready",
    )

    assert result.returncode == 2
    assert "conflicts with legacy DEPLOY_RUNTIME_PROFILE" in result.stderr
