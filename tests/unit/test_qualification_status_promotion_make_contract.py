from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _make_dry_run(*args: str) -> str:
    result = subprocess.run(
        ["make", "-n", "qualification-status-promote", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_status_promotion_make_defaults_to_plan_only() -> None:
    output = _make_dry_run("PROFILE=profile-under-review")

    assert "scripts/qualification/status_promotion.py --profile \"profile-under-review\"" in output
    assert "--apply" not in output
    assert "--confirm" not in output


def test_status_promotion_make_apply_forwards_exact_reviewed_digest() -> None:
    output = _make_dry_run(
        "PROFILE=profile-under-review",
        "APPLY=1",
        "CONFIRM=reviewed-plan-digest",
    )

    assert "scripts/qualification/status_promotion.py --profile \"profile-under-review\"" in output
    assert "--apply --confirm \"reviewed-plan-digest\"" in output
