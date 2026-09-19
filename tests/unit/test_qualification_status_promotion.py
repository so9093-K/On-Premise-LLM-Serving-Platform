from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.qualification.status_promotion import apply_plan, build_plan


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


def test_apply_requires_exact_reviewed_plan_and_only_promotes_status(tmp_path: Path) -> None:
    _write_configs(tmp_path)
    path = tmp_path / "configs/main_model_profiles.yaml"
    before = path.read_text(encoding="utf-8")

    with patch(
        "scripts.qualification.status_promotion.eligible_qualified_run_ids",
        return_value=["qualified-run-1"],
    ):
        plan = build_plan("gemma-test", root=tmp_path)
        applied = apply_plan("gemma-test", plan.plan_digest, root=tmp_path)

    assert applied == plan
    assert path.read_text(encoding="utf-8") == before.replace(
        "      status: unverified\n", "      status: verified\n"
    )


def test_apply_rejects_repository_drift_without_mutation(tmp_path: Path) -> None:
    _write_configs(tmp_path)
    path = tmp_path / "configs/main_model_profiles.yaml"

    with patch(
        "scripts.qualification.status_promotion.eligible_qualified_run_ids",
        return_value=["qualified-run-1"],
    ):
        plan = build_plan("gemma-test", root=tmp_path)
        path.write_text(path.read_text(encoding="utf-8") + "# changed after review\n", encoding="utf-8")
        drifted = path.read_bytes()
        with pytest.raises(ValueError, match="no longer matches"):
            apply_plan("gemma-test", plan.plan_digest, root=tmp_path)

    assert path.read_bytes() == drifted
