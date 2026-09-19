from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.qualification.status_promotion import build_plan


def _write_configs(root: Path) -> None:
    configs = root / "configs"
    configs.mkdir()
    (configs / "main_model_profiles.yaml").write_text(
        "version: 1\nprofiles:\n  gemma-test:\n    qualification:\n      status: unverified\n",
        encoding="utf-8",
    )
    (configs / "qualification_evidence.yaml").write_text(
        "version: 1\nrecords: {}\n", encoding="utf-8"
    )
    (configs / "deployment_targets.yaml").write_text("version: 1\n", encoding="utf-8")
    (configs / "qualification_checks.yaml").write_text("version: 1\n", encoding="utf-8")


def test_plan_is_review_only_and_binds_current_repository_state(tmp_path: Path) -> None:
    _write_configs(tmp_path)
    profiles_path = tmp_path / "configs/main_model_profiles.yaml"
    before = profiles_path.read_bytes()

    with patch(
        "scripts.qualification.status_promotion.eligible_qualified_run_ids",
        return_value=["qualified-run-1"],
    ) as eligibility:
        plan = build_plan("gemma-test", root=tmp_path)

    assert plan.profile_id == "gemma-test"
    assert plan.from_status == "unverified"
    assert plan.to_status == "verified"
    assert plan.qualified_run_ids == ["qualified-run-1"]
    assert len(plan.profiles_digest) == 64
    assert len(plan.evidence_digest) == 64
    assert len(plan.plan_digest) == 64
    assert profiles_path.read_bytes() == before
    eligibility.assert_called_once()


def test_plan_digest_changes_when_profile_state_changes(tmp_path: Path) -> None:
    _write_configs(tmp_path)
    with patch(
        "scripts.qualification.status_promotion.eligible_qualified_run_ids",
        return_value=["qualified-run-1"],
    ):
        first = build_plan("gemma-test", root=tmp_path)
        path = tmp_path / "configs/main_model_profiles.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "# reviewed state changed\n", encoding="utf-8")
        second = build_plan("gemma-test", root=tmp_path)

    assert first.profiles_digest != second.profiles_digest
    assert first.plan_digest != second.plan_digest
