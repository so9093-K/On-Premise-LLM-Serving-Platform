from __future__ import annotations

import copy

import pytest

from scripts.validation.governance.qualification import (
    validate_qualification_evidence,
    validate_qualification_evidence_document,
)


def _profiles() -> dict:
    return {
        "profiles": {
            "candidate": {
                "model_id": "org/model",
                "revision": "a" * 40,
                "qualification": {"status": "verified"},
                "capabilities": {"deployed_input": ["text", "image"]},
            }
        }
    }


def _unverified_profiles() -> dict:
    profiles = copy.deepcopy(_profiles())
    profiles["profiles"]["candidate"]["qualification"]["status"] = "unverified"
    return profiles


def _targets() -> dict:
    return {"targets": {"linux-nvidia-dynamic": {}}}


def _legacy_record() -> dict:
    return {
        "version": 1,
        "records": {
            "candidate-legacy": {
                "kind": "legacy_backfill",
                "subject": {
                    "type": "main_model_profile",
                    "profile_id": "candidate",
                    "model_id": "org/model",
                    "revision": "a" * 40,
                },
                "deployment_target": "linux-nvidia-dynamic",
                "capabilities": ["text", "image"],
                "result": "passed",
                "source": {
                    "path": "configs/main_model_profiles.yaml",
                    "note": "legacy verification note",
                },
            }
        },
    }


def _required_check_results(*, image_status: str = "passed") -> list[dict[str, str]]:
    return [
        {"id": "main_model.runtime.models", "status": "passed"},
        {"id": "main_model.gateway.models", "status": "passed"},
        {"id": "main_model.chat.text", "status": "passed"},
        {"id": "main_model.chat.image", "status": image_status},
    ]


def _qualified_record(*, result: str = "passed") -> dict:
    evidence = _legacy_record()
    record = evidence["records"]["candidate-legacy"]
    record["kind"] = "qualified_run"
    record["validated_at"] = "2026-09-18T09:00:00Z"
    record["runtime"] = {
        "engine": "vllm",
        "version": "0.25.1",
        "image_digest": "sha256:" + "b" * 64,
    }
    record["checks"] = _required_check_results()
    record["hardware"] = {
        "gpu": "NVIDIA RTX 6000 Ada Generation",
        "driver_version": "580.65.06",
    }
    record["result"] = result
    return evidence


def test_repository_qualification_evidence_is_valid() -> None:
    validate_qualification_evidence()


def test_verified_profile_requires_matching_current_evidence() -> None:
    evidence = _legacy_record()
    evidence["records"]["candidate-legacy"]["subject"]["revision"] = "b" * 40

    with pytest.raises(SystemExit, match="requires passed qualification evidence"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_failed_record_does_not_qualify_verified_profile() -> None:
    evidence = _legacy_record()
    evidence["records"]["candidate-legacy"]["result"] = "failed"

    with pytest.raises(SystemExit, match="requires passed qualification evidence"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_capability_set_must_match_current_profile() -> None:
    evidence = _legacy_record()
    evidence["records"]["candidate-legacy"]["capabilities"] = ["text"]

    with pytest.raises(SystemExit, match="requires passed qualification evidence"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_qualified_run_requires_immutable_runtime_and_hardware_fingerprint() -> None:
    evidence = _qualified_record()
    record = evidence["records"]["candidate-legacy"]
    record["runtime"]["image_digest"] = "not-a-digest"
    record["hardware"]["driver_version"] = "unknown"

    with pytest.raises(SystemExit, match="runtime.image_digest must be sha256"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_complete_qualified_run_is_accepted() -> None:
    validate_qualification_evidence_document(
        _qualified_record(),
        _profiles(),
        _targets(),
    )


def test_qualified_run_requires_named_check_results() -> None:
    evidence = _qualified_record()
    evidence["records"]["candidate-legacy"].pop("checks")

    with pytest.raises(SystemExit, match="checks must be a non-empty check result list"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_qualified_run_rejects_unknown_check_id() -> None:
    evidence = _qualified_record()
    evidence["records"]["candidate-legacy"]["checks"].append(
        {"id": "main_model.unknown", "status": "passed"}
    )

    with pytest.raises(SystemExit, match="unknown qualification check"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_qualified_run_requires_all_checks_for_declared_capabilities() -> None:
    evidence = _qualified_record()
    evidence["records"]["candidate-legacy"]["checks"] = [
        item
        for item in evidence["records"]["candidate-legacy"]["checks"]
        if item["id"] != "main_model.chat.image"
    ]

    with pytest.raises(SystemExit, match="missing required qualification checks"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_passed_qualified_run_rejects_skipped_required_check() -> None:
    evidence = _qualified_record()
    evidence["records"]["candidate-legacy"]["checks"] = _required_check_results(
        image_status="skipped"
    )

    with pytest.raises(SystemExit, match="requires every required check to pass"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_failed_qualified_run_can_preserve_skipped_required_check() -> None:
    evidence = _qualified_record(result="failed")
    evidence["records"]["candidate-legacy"]["checks"] = _required_check_results(
        image_status="skipped"
    )

    validate_qualification_evidence_document(
        evidence,
        _unverified_profiles(),
        _targets(),
    )


def test_failed_qualified_run_requires_non_pass_check() -> None:
    evidence = _qualified_record(result="failed")

    with pytest.raises(SystemExit, match="requires at least one failed or skipped check"):
        validate_qualification_evidence_document(
            evidence,
            _unverified_profiles(),
            _targets(),
        )


def test_qualified_run_rejects_capability_without_required_check_mapping() -> None:
    evidence = _qualified_record()
    record = evidence["records"]["candidate-legacy"]
    record["capabilities"].append("depth")

    with pytest.raises(SystemExit, match="has no required-check mapping"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_check_registry_rejects_unknown_requirement_reference() -> None:
    checks = {
        "version": 1,
        "checks": {
            "main_model.chat.text": {"description": "text canary"},
        },
        "capability_requirements": {
            "text": ["main_model.chat.missing"],
        },
    }

    with pytest.raises(SystemExit, match="references unknown checks"):
        validate_qualification_evidence_document(
            _legacy_record(),
            _profiles(),
            _targets(),
            checks,
        )


def test_legacy_history_can_reference_removed_target_but_not_qualify_current_profile() -> None:
    evidence = _legacy_record()
    record = evidence["records"]["candidate-legacy"]
    record["deployment_target"] = "retired-target"

    with pytest.raises(SystemExit, match="requires passed qualification evidence"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_unverified_profile_does_not_require_current_passing_evidence() -> None:
    evidence = _legacy_record()
    evidence["records"]["candidate-legacy"]["result"] = "failed"

    validate_qualification_evidence_document(evidence, _unverified_profiles(), _targets())
