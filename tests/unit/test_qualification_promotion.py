from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.qualification.produce_candidate import candidate_record_id
from scripts.qualification.promote_candidate import (
    QualificationPromotionError,
    apply_promotion,
    load_candidate,
    plan_candidate,
)


def _receipt(*, result: str = "passed") -> dict[str, object]:
    return {
        "version": 1,
        "kind": "qualification_run_receipt",
        "record": {
            "kind": "qualified_run",
            "subject": {
                "type": "main_model_profile",
                "profile_id": "gemma-test",
                "model_id": "example/model",
                "revision": "abc123",
            },
            "deployment_target": "linux-nvidia-dynamic",
            "capabilities": ["text"],
            "result": result,
            "validated_at": "2026-09-19T06:00:00+00:00",
            "runtime": {
                "engine": "vllm",
                "version": "1.0.0",
                "image_digest": "sha256:" + "a" * 64,
            },
            "checks": [{"id": "main_model.runtime.models", "status": "passed"}],
            "hardware": {"gpu": "Example GPU", "driver_version": "999.1"},
        },
    }


def _write_candidate(root: Path, receipt: dict[str, object]) -> Path:
    record_id = candidate_record_id(receipt)
    path = root / "reports/qualification" / f"{record_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def _write_minimal_configs(root: Path) -> None:
    config_dir = root / "configs"
    config_dir.mkdir()
    (config_dir / "qualification_evidence.yaml").write_text(
        "version: 1\nrecords:\n  existing:\n    kind: legacy_backfill\n",
        encoding="utf-8",
    )
    for name in ("main_model_profiles.yaml", "deployment_targets.yaml", "qualification_checks.yaml"):
        (config_dir / name).write_text("version: 1\n", encoding="utf-8")


def test_failed_candidate_cannot_be_promoted(tmp_path: Path) -> None:
    path = _write_candidate(tmp_path, _receipt(result="failed"))
    with pytest.raises(QualificationPromotionError, match="only a passed"):
        load_candidate(path)


def test_candidate_filename_is_current_contract_identity(tmp_path: Path) -> None:
    receipt = _receipt()
    path = tmp_path / "wrong-name.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(QualificationPromotionError, match="deterministic record id"):
        load_candidate(path)


def test_plan_is_review_only_and_does_not_mutate_repository(tmp_path: Path) -> None:
    _write_minimal_configs(tmp_path)
    receipt = _receipt()
    candidate = _write_candidate(tmp_path, receipt)
    catalog_path = tmp_path / "configs/qualification_evidence.yaml"
    catalog_before = catalog_path.read_bytes()

    with patch(
        "scripts.qualification.promote_candidate.validate_qualification_evidence_document"
    ) as validate:
        plan = plan_candidate(candidate, root=tmp_path)

    assert plan.record_id == candidate_record_id(receipt)
    assert len(plan.plan_digest) == 64
    assert plan.destination == (
        tmp_path / "evidence/qualification/runs" / f"{plan.record_id}.json"
    )
    assert not plan.destination.exists()
    assert catalog_path.read_bytes() == catalog_before
    validate.assert_called_once()


def test_apply_requires_exact_reviewed_plan_digest_and_writes_matching_evidence(
    tmp_path: Path,
) -> None:
    _write_minimal_configs(tmp_path)
    receipt = _receipt()
    candidate = _write_candidate(tmp_path, receipt)

    with patch(
        "scripts.qualification.promote_candidate.validate_qualification_evidence_document"
    ):
        plan = plan_candidate(candidate, root=tmp_path)
        with pytest.raises(QualificationPromotionError, match="confirmation digest"):
            apply_promotion(plan, root=tmp_path, confirm_digest="wrong")
        promoted_id, destination = apply_promotion(
            plan,
            root=tmp_path,
            confirm_digest=plan.plan_digest,
        )

    assert promoted_id == plan.record_id
    assert json.loads(destination.read_text(encoding="utf-8")) == receipt
    catalog = yaml.safe_load(
        (tmp_path / "configs/qualification_evidence.yaml").read_text(encoding="utf-8")
    )
    promoted = catalog["records"][plan.record_id]
    assert promoted["source"] == {
        "path": f"evidence/qualification/runs/{plan.record_id}.json",
        "note": "reviewed live qualification receipt",
    }
    assert {key: value for key, value in promoted.items() if key != "source"} == receipt["record"]


def test_apply_rejects_candidate_content_changed_after_review(tmp_path: Path) -> None:
    _write_minimal_configs(tmp_path)
    receipt = _receipt()
    candidate = _write_candidate(tmp_path, receipt)

    with patch(
        "scripts.qualification.promote_candidate.validate_qualification_evidence_document"
    ):
        plan = plan_candidate(candidate, root=tmp_path)
        changed = deepcopy(receipt)
        changed["record"]["hardware"]["driver_version"] = "999.2"
        candidate.write_text(json.dumps(changed), encoding="utf-8")

        with pytest.raises(QualificationPromotionError, match="plan changed"):
            apply_promotion(
                plan,
                root=tmp_path,
                confirm_digest=plan.plan_digest,
            )


def test_matching_orphan_receipt_can_be_recovered_by_catalog_apply(tmp_path: Path) -> None:
    _write_minimal_configs(tmp_path)
    receipt = _receipt()
    candidate = _write_candidate(tmp_path, receipt)
    record_id = candidate_record_id(receipt)
    orphan = tmp_path / "evidence/qualification/runs" / f"{record_id}.json"
    orphan.parent.mkdir(parents=True)
    orphan.write_text(json.dumps(receipt), encoding="utf-8")

    with patch(
        "scripts.qualification.promote_candidate.validate_qualification_evidence_document"
    ):
        plan = plan_candidate(candidate, root=tmp_path)
        assert plan.orphan_receipt is True
        promoted_id, destination = apply_promotion(
            plan,
            root=tmp_path,
            confirm_digest=plan.plan_digest,
        )

    assert promoted_id == record_id
    assert destination == orphan
    catalog = yaml.safe_load(
        (tmp_path / "configs/qualification_evidence.yaml").read_text(encoding="utf-8")
    )
    assert record_id in catalog["records"]


def test_promotion_refuses_existing_catalog_identity(tmp_path: Path) -> None:
    receipt = _receipt()
    candidate = _write_candidate(tmp_path, receipt)
    record_id = candidate_record_id(receipt)
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "qualification_evidence.yaml").write_text(
        yaml.safe_dump({"version": 1, "records": {record_id: {"kind": "qualified_run"}}}),
        encoding="utf-8",
    )

    with pytest.raises(QualificationPromotionError, match="record already exists"):
        plan_candidate(candidate, root=tmp_path)


def test_promotion_input_must_stay_under_reviewable_candidate_directory(
    tmp_path: Path,
) -> None:
    _write_minimal_configs(tmp_path)
    receipt = _receipt()
    candidate = tmp_path / "outside.json"
    candidate.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(QualificationPromotionError, match="reports/qualification"):
        plan_candidate(candidate, root=tmp_path)
