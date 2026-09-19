from __future__ import annotations

import copy

import pytest

from scripts.qualification.context import qualification_context_for_runtime
from scripts.qualification.hardware import (
    GpuObservation,
    QualificationHardwareError,
    parse_nvidia_smi_output,
)
from scripts.qualification.produce_candidate import (
    QualificationCandidateError,
    build_candidate_receipt,
    write_candidate,
)


KNOWN = {
    "main_model.runtime.models",
    "main_model.gateway.models",
    "main_model.chat.text",
    "main_model.chat.image",
}
REQUIREMENTS = {
    "text": frozenset(
        {
            "main_model.runtime.models",
            "main_model.gateway.models",
            "main_model.chat.text",
        }
    ),
    "image": frozenset({"main_model.chat.image"}),
}
GPU = GpuObservation(
    name="NVIDIA GeForce RTX 4090",
    uuid="GPU-01234567-89ab-cdef-0123-456789abcdef",
    memory_total_mib=24564,
    driver_version="580.65.06",
)


def _result(check_id: str, status: str = "pass", **details) -> dict:
    return {
        "category": "qualification",
        "name": check_id,
        "status": status,
        "latency_ms": 1,
        "detail": "",
        "details": details,
        "qualification_check_id": check_id,
    }


def _report(status=None) -> dict:
    status = status or _status()
    context = qualification_context_for_runtime(
        status,
        deployment_target="linux-nvidia-dynamic",
        hardware=GPU.as_context(),
    )
    return {
        "version": "0.0.1",
        "started_at": "2026-09-19T01:00:00+00:00",
        "finished_at": "2026-09-19T01:05:00+00:00",
        "mode": "live",
        "qualification_context": {
            "started": context,
            "finished": copy.deepcopy(context),
            "stable": True,
            "errors": [],
        },
        "summary": {"passed": 4, "failed": 0, "skipped": 0, "degraded_features": []},
        "results": [
            _result("main_model.runtime.models"),
            _result(
                "main_model.gateway.models",
                main_model_input_modalities=["text", "image"],
            ),
            _result("main_model.chat.text"),
            _result("main_model.chat.image"),
        ],
    }


def _status() -> dict:
    digest = "sha256:" + "d" * 64
    image = "registry.example.com/vllm@" + digest
    return {
        "public_model": "local-main",
        "active_profile": {
            "id": "gemma4-e4b-it",
            "upstream_model_id": "google/gemma-4-E4B-it",
            "revision": "a" * 40,
            "runtime_image": image,
            "capabilities": {"deployed_input": ["text", "image"]},
        },
        "gate": "open",
        "runtime_state": "active",
        "observed_runtime": {
            "status": "ready",
            "health": "healthy",
            "profile_id": "gemma4-e4b-it",
            "image_ref": image,
            "image_id": "sha256:" + "e" * 64,
            "image_digest": digest,
            "runtime_engine": {"name": "vllm", "version": "0.25.1"},
        },
    }


def _build(report=None, status=None):
    current_status = status or _status()
    return build_candidate_receipt(
        report=report or _report(current_status),
        main_model_status=current_status,
        deployment_target="linux-nvidia-dynamic",
        known_checks=KNOWN,
        capability_requirements=REQUIREMENTS,
        gpu=GPU,
    )


def test_complete_runtime_state_produces_passed_reviewable_receipt() -> None:
    receipt = _build()

    assert receipt["version"] == 1
    assert receipt["kind"] == "qualification_run_receipt"
    record = receipt["record"]
    assert record["result"] == "passed"
    assert record["subject"]["profile_id"] == "gemma4-e4b-it"
    assert record["runtime"]["image_digest"] == "sha256:" + "d" * 64
    assert record["hardware"] == {
        "gpu": "NVIDIA GeForce RTX 4090",
        "driver_version": "580.65.06",
    }
    assert record["observations"]["runtime_image_id"] == "sha256:" + "e" * 64
    assert "source" not in record


def test_required_skipped_check_creates_failed_candidate() -> None:
    report = _report()
    report["results"][-1]["status"] = "skip"

    receipt = _build(report=report)

    assert receipt["record"]["result"] == "failed"
    assert {
        item["id"]: item["status"] for item in receipt["record"]["checks"]
    }["main_model.chat.image"] == "skipped"


def test_missing_required_check_is_producer_error() -> None:
    report = _report()
    report["results"] = [
        row
        for row in report["results"]
        if row["qualification_check_id"] != "main_model.chat.image"
    ]

    with pytest.raises(QualificationCandidateError, match="missing required"):
        _build(report=report)


def test_unknown_qualification_check_is_producer_error() -> None:
    report = _report()
    report["results"].append(_result("main_model.unknown"))

    with pytest.raises(QualificationCandidateError, match="unknown qualification check"):
        _build(report=report)


def test_current_profile_drift_is_producer_error() -> None:
    report = _report()
    status = _status()
    status["active_profile"]["id"] = "other-profile"
    status["observed_runtime"]["profile_id"] = "other-profile"

    with pytest.raises(QualificationCandidateError, match="qualification context"):
        _build(report=report, status=status)


def test_report_from_different_gpu_is_producer_error() -> None:
    report = _report()
    report["qualification_context"]["started"]["hardware"]["gpu_uuid"] = "GPU-other"
    report["qualification_context"]["finished"]["hardware"]["gpu_uuid"] = "GPU-other"

    with pytest.raises(QualificationCandidateError, match="does not match current Main Model"):
        _build(report=report)


def test_report_rejects_profile_change_during_validation() -> None:
    report = _report()
    report["qualification_context"]["finished"]["profile_id"] = "other-profile"
    report["qualification_context"]["stable"] = False

    with pytest.raises(QualificationCandidateError, match="must be stable"):
        _build(report=report)


def test_missing_distribution_digest_is_producer_error() -> None:
    report = _report()
    status = _status()
    status["observed_runtime"]["image_digest"] = None

    with pytest.raises(QualificationCandidateError, match="image_digest"):
        _build(report=report, status=status)


def test_report_modality_drift_is_producer_error() -> None:
    report = _report()
    gateway = next(
        row
        for row in report["results"]
        if row["qualification_check_id"] == "main_model.gateway.models"
    )
    gateway["details"]["main_model_input_modalities"] = ["text"]

    with pytest.raises(QualificationCandidateError, match="modalities"):
        _build(report=report)


def test_candidate_writer_cannot_write_repository_evidence_directly(tmp_path) -> None:
    receipt = _build()

    with pytest.raises(QualificationCandidateError, match="reports/qualification"):
        write_candidate(
            receipt,
            root=tmp_path,
            output_dir="evidence/qualification/runs",
        )


def test_multiple_visible_gpus_are_not_guessed() -> None:
    output = (
        "NVIDIA GeForce RTX 4090, GPU-a, 24564, 580.65.06\n"
        "NVIDIA GeForce RTX 4090, GPU-b, 24564, 580.65.06\n"
    )

    with pytest.raises(QualificationHardwareError, match="exactly one visible NVIDIA GPU"):
        parse_nvidia_smi_output(output)
