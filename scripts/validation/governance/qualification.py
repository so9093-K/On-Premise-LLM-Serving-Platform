from __future__ import annotations

import re
from typing import Any

from .common import ROOT, read_yaml


_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][^\s]+)?$")


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_record(record_id: str, record: object, targets: set[str]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise SystemExit(f"qualification evidence {record_id!r} must be a mapping")

    kind = record.get("kind")
    if kind not in {"legacy_backfill", "qualified_run"}:
        raise SystemExit(
            f"qualification evidence {record_id!r}.kind must be legacy_backfill or qualified_run"
        )

    subject = record.get("subject")
    if not isinstance(subject, dict):
        raise SystemExit(f"qualification evidence {record_id!r}.subject must be a mapping")
    required_subject = {"type", "profile_id", "model_id", "revision"}
    missing_subject = required_subject - set(subject)
    if missing_subject:
        raise SystemExit(
            f"qualification evidence {record_id!r}.subject missing: "
            + ", ".join(sorted(missing_subject))
        )
    if subject.get("type") != "main_model_profile":
        raise SystemExit(
            f"qualification evidence {record_id!r}.subject.type must be main_model_profile"
        )
    for key in ("profile_id", "model_id", "revision"):
        if not _non_empty_string(subject.get(key)):
            raise SystemExit(
                f"qualification evidence {record_id!r}.subject.{key} must be non-empty"
            )

    deployment_target = record.get("deployment_target")
    if not _non_empty_string(deployment_target):
        raise SystemExit(
            f"qualification evidence {record_id!r}.deployment_target must be non-empty"
        )

    capabilities = record.get("capabilities")
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or not all(_non_empty_string(item) for item in capabilities)
        or len(set(capabilities)) != len(capabilities)
    ):
        raise SystemExit(
            f"qualification evidence {record_id!r}.capabilities must be a unique non-empty string list"
        )

    if record.get("result") not in {"passed", "failed"}:
        raise SystemExit(
            f"qualification evidence {record_id!r}.result must be passed or failed"
        )

    source = record.get("source")
    if not isinstance(source, dict):
        raise SystemExit(f"qualification evidence {record_id!r}.source must be a mapping")
    if not _non_empty_string(source.get("path")) or not _non_empty_string(source.get("note")):
        raise SystemExit(
            f"qualification evidence {record_id!r}.source.path and source.note must be non-empty"
        )
    source_path = ROOT / str(source["path"])
    if not source_path.exists():
        raise SystemExit(
            f"qualification evidence {record_id!r}.source.path does not exist: {source['path']}"
        )

    validated_at = record.get("validated_at")
    if validated_at is not None and (
        not _non_empty_string(validated_at) or not _DATE.match(str(validated_at))
    ):
        raise SystemExit(
            f"qualification evidence {record_id!r}.validated_at must be an ISO-like date/time string"
        )

    hardware = record.get("hardware")
    if hardware is not None and not isinstance(hardware, dict):
        raise SystemExit(f"qualification evidence {record_id!r}.hardware must be a mapping")

    if kind == "qualified_run":
        if not _non_empty_string(validated_at):
            raise SystemExit(
                f"qualification evidence {record_id!r} qualified_run requires validated_at"
            )
        runtime = record.get("runtime")
        if not isinstance(runtime, dict):
            raise SystemExit(
                f"qualification evidence {record_id!r} qualified_run requires runtime mapping"
            )
        for key in ("engine", "version"):
            if not _non_empty_string(runtime.get(key)):
                raise SystemExit(
                    f"qualification evidence {record_id!r}.runtime.{key} must be non-empty"
                )
        image_digest = runtime.get("image_digest")
        if not _non_empty_string(image_digest) or not _IMAGE_DIGEST.match(str(image_digest)):
            raise SystemExit(
                f"qualification evidence {record_id!r}.runtime.image_digest must be sha256:<64 hex>"
            )
        if deployment_target not in targets:
            raise SystemExit(
                f"qualification evidence {record_id!r} qualified_run deployment_target "
                "must reference configs/deployment_targets.yaml"
            )
        checks = record.get("checks")
        if (
            not isinstance(checks, list)
            or not checks
            or not all(_non_empty_string(item) for item in checks)
            or len(set(checks)) != len(checks)
        ):
            raise SystemExit(
                f"qualification evidence {record_id!r}.checks must be a unique non-empty "
                "string list for qualified_run"
            )
        if not isinstance(hardware, dict):
            raise SystemExit(
                f"qualification evidence {record_id!r} qualified_run requires hardware mapping"
            )
        for key in ("gpu", "driver_version"):
            if not _non_empty_string(hardware.get(key)):
                raise SystemExit(
                    f"qualification evidence {record_id!r}.hardware.{key} must be non-empty"
                )

    return record


def validate_qualification_evidence_document(
    document: object,
    profiles_document: object,
    deployment_targets_document: object,
) -> None:
    if not isinstance(document, dict) or document.get("version") != 1:
        raise SystemExit("qualification_evidence.yaml must declare version: 1")
    records = document.get("records")
    if not isinstance(records, dict) or not records:
        raise SystemExit("qualification_evidence.yaml must declare non-empty records")

    if not isinstance(profiles_document, dict):
        raise SystemExit("main_model_profiles.yaml must be a mapping")
    profiles = profiles_document.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise SystemExit("main_model_profiles.yaml must declare profiles")

    if not isinstance(deployment_targets_document, dict):
        raise SystemExit("deployment_targets.yaml must be a mapping")
    targets_document = deployment_targets_document.get("targets")
    if not isinstance(targets_document, dict) or not targets_document:
        raise SystemExit("deployment_targets.yaml must declare targets")
    targets = set(str(target) for target in targets_document)

    validated_records: list[dict[str, Any]] = []
    for record_id, raw_record in records.items():
        if not _non_empty_string(record_id):
            raise SystemExit("qualification evidence record ids must be non-empty strings")
        validated_records.append(_validate_record(str(record_id), raw_record, targets))

    for profile_id, profile in profiles.items():
        if not isinstance(profile, dict):
            raise SystemExit(f"main model profile {profile_id!r} must be a mapping")
        qualification = profile.get("qualification")
        if not isinstance(qualification, dict):
            continue
        if qualification.get("status") != "verified":
            continue

        deployed_input = profile.get("capabilities", {}).get("deployed_input", [])
        if not isinstance(deployed_input, list) or not deployed_input:
            raise SystemExit(
                f"verified main model profile {profile_id!r} must declare deployed_input capabilities"
            )
        expected_capabilities = set(str(item) for item in deployed_input)
        expected_model_id = str(profile.get("model_id", ""))
        expected_revision = str(profile.get("revision", ""))

        matches = []
        for record in validated_records:
            subject = record["subject"]
            if (
                subject["profile_id"] == profile_id
                and subject["model_id"] == expected_model_id
                and subject["revision"] == expected_revision
                and record["result"] == "passed"
                and record["deployment_target"] in targets
                and set(record["capabilities"]) == expected_capabilities
            ):
                matches.append(record)

        if not matches:
            raise SystemExit(
                f"verified main model profile {profile_id!r} requires passed qualification evidence "
                "matching current model_id, revision, and deployed_input capabilities"
            )


def validate_qualification_evidence() -> None:
    validate_qualification_evidence_document(
        read_yaml("configs/qualification_evidence.yaml"),
        read_yaml("configs/main_model_profiles.yaml"),
        read_yaml("configs/deployment_targets.yaml"),
    )
