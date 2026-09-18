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
    evidence = _legacy_record()
    record = evidence["records"]["candidate-legacy"]
    record["kind"] = "qualified_run"
    record["validated_at"] = "2026-09-18"
    record["runtime"] = {
        "engine": "vllm",
        "version": "0.25.1",
        "image_digest": "not-a-digest",
    }
    record["checks"] = ["models", "chat"]
    record["hardware"] = {
        "gpu": "NVIDIA RTX 6000 Ada Generation",
        "driver_version": "unknown",
    }

    with pytest.raises(SystemExit, match="runtime.image_digest must be sha256"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_complete_qualified_run_is_accepted() -> None:
    evidence = _legacy_record()
    record = evidence["records"]["candidate-legacy"]
    record["kind"] = "qualified_run"
    record["validated_at"] = "2026-09-18T09:00:00Z"
    record["runtime"] = {
        "engine": "vllm",
        "version": "0.25.1",
        "image_digest": "sha256:" + "b" * 64,
    }
    record["checks"] = ["models", "chat", "image"]
    record["hardware"] = {
        "gpu": "NVIDIA RTX 6000 Ada Generation",
        "driver_version": "580.65.06",
    }

    validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_qualified_run_requires_named_checks() -> None:
    evidence = _legacy_record()
    record = evidence["records"]["candidate-legacy"]
    record["kind"] = "qualified_run"
    record["validated_at"] = "2026-09-18"
    record["runtime"] = {
        "engine": "vllm",
        "version": "0.25.1",
        "image_digest": "sha256:" + "b" * 64,
    }
    record["hardware"] = {
        "gpu": "NVIDIA RTX 6000 Ada Generation",
        "driver_version": "580.65.06",
    }

    with pytest.raises(SystemExit, match="checks must be a unique non-empty"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_legacy_history_can_reference_removed_target_but_not_qualify_current_profile() -> None:
    evidence = _legacy_record()
    record = evidence["records"]["candidate-legacy"]
    record["deployment_target"] = "retired-target"

    with pytest.raises(SystemExit, match="requires passed qualification evidence"):
        validate_qualification_evidence_document(evidence, _profiles(), _targets())


def test_unverified_profile_does_not_require_current_passing_evidence() -> None:
    profiles = copy.deepcopy(_profiles())
    profiles["profiles"]["candidate"]["qualification"]["status"] = "unverified"
    evidence = _legacy_record()
    evidence["records"]["candidate-legacy"]["result"] = "failed"

    validate_qualification_evidence_document(evidence, profiles, _targets())
