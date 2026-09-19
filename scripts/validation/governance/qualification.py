from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .common import ROOT, read_yaml


_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][^\s]+)?$")
_CHECK_STATUSES = {"passed", "failed", "skipped"}
_QUALIFICATION_RECEIPT_DIR = Path("evidence/qualification/runs")


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_check_registry(
    document: object,
) -> tuple[set[str], dict[str, frozenset[str]]]:
    if not isinstance(document, dict) or document.get("version") != 1:
        raise SystemExit("qualification_checks.yaml must declare version: 1")

    checks = document.get("checks")
    if not isinstance(checks, dict) or not checks:
        raise SystemExit("qualification_checks.yaml must declare non-empty checks")

    known_checks: set[str] = set()
    for check_id, metadata in checks.items():
        if not _non_empty_string(check_id):
            raise SystemExit("qualification check ids must be non-empty strings")
        if not isinstance(metadata, dict):
            raise SystemExit(
                f"qualification check {check_id!r} metadata must be a mapping"
            )
        if not _non_empty_string(metadata.get("description")):
            raise SystemExit(
                f"qualification check {check_id!r}.description must be non-empty"
            )
        known_checks.add(str(check_id))

    raw_requirements = document.get("capability_requirements")
    if not isinstance(raw_requirements, dict) or not raw_requirements:
        raise SystemExit(
            "qualification_checks.yaml must declare non-empty capability_requirements"
        )

    requirements: dict[str, frozenset[str]] = {}
    for capability, raw_checks in raw_requirements.items():
        if not _non_empty_string(capability):
            raise SystemExit(
                "qualification capability requirement keys must be non-empty strings"
            )
        if (
            not isinstance(raw_checks, list)
            or not raw_checks
            or not all(_non_empty_string(item) for item in raw_checks)
            or len(set(raw_checks)) != len(raw_checks)
        ):
            raise SystemExit(
                f"qualification capability {capability!r} must require a unique "
                "non-empty check-id list"
            )
        unknown = sorted(set(str(item) for item in raw_checks) - known_checks)
        if unknown:
            raise SystemExit(
                f"qualification capability {capability!r} references unknown checks: "
                + ", ".join(unknown)
            )
        requirements[str(capability)] = frozenset(str(item) for item in raw_checks)

    return known_checks, requirements


def load_qualification_check_contract(
    document: object | None = None,
) -> tuple[set[str], dict[str, frozenset[str]]]:
    """Repository validator와 producer가 같은 stable-check 계약을 사용한다."""
    checks_document = (
        read_yaml("configs/qualification_checks.yaml")
        if document is None
        else document
    )
    return _validate_check_registry(checks_document)


def _validate_qualified_run_checks(
    record_id: str,
    record: dict[str, Any],
    *,
    known_checks: set[str],
    capability_requirements: dict[str, frozenset[str]],
) -> None:
    checks = record.get("checks")
    if not isinstance(checks, list) or not checks:
        raise SystemExit(
            f"qualification evidence {record_id!r}.checks must be a non-empty "
            "check result list for qualified_run"
        )

    statuses: dict[str, str] = {}
    for index, item in enumerate(checks):
        if not isinstance(item, dict):
            raise SystemExit(
                f"qualification evidence {record_id!r}.checks[{index}] must be a mapping"
            )
        allowed_fields = {"id", "status", "note"}
        unknown_fields = set(item) - allowed_fields
        if unknown_fields:
            raise SystemExit(
                f"qualification evidence {record_id!r}.checks[{index}] has unknown fields: "
                + ", ".join(sorted(unknown_fields))
            )
        check_id = item.get("id")
        status = item.get("status")
        if not _non_empty_string(check_id):
            raise SystemExit(
                f"qualification evidence {record_id!r}.checks[{index}].id must be non-empty"
            )
        check_id = str(check_id)
        if check_id not in known_checks:
            raise SystemExit(
                f"qualification evidence {record_id!r} references unknown qualification "
                f"check {check_id!r}"
            )
        if status not in _CHECK_STATUSES:
            raise SystemExit(
                f"qualification evidence {record_id!r}.checks[{index}].status must be "
                "passed, failed, or skipped"
            )
        if check_id in statuses:
            raise SystemExit(
                f"qualification evidence {record_id!r}.checks contains duplicate id "
                f"{check_id!r}"
            )
        note = item.get("note")
        if note is not None and not _non_empty_string(note):
            raise SystemExit(
                f"qualification evidence {record_id!r}.checks[{index}].note must be "
                "non-empty when present"
            )
        statuses[check_id] = str(status)

    required: set[str] = set()
    for capability in record["capabilities"]:
        required_for_capability = capability_requirements.get(str(capability))
        if required_for_capability is None:
            raise SystemExit(
                f"qualification evidence {record_id!r} capability {capability!r} "
                "has no required-check mapping"
            )
        required.update(required_for_capability)

    missing = sorted(required - statuses.keys())
    if missing:
        raise SystemExit(
            f"qualification evidence {record_id!r} missing required qualification checks: "
            + ", ".join(missing)
        )

    if record["result"] == "passed":
        non_passed_required = sorted(
            check_id for check_id in required if statuses[check_id] != "passed"
        )
        if non_passed_required:
            details = ", ".join(
                f"{check_id}={statuses[check_id]}" for check_id in non_passed_required
            )
            raise SystemExit(
                f"qualification evidence {record_id!r} passed result requires every "
                f"required check to pass; got {details}"
            )
        non_passed_recorded = sorted(
            check_id for check_id, status in statuses.items() if status != "passed"
        )
        if non_passed_recorded:
            raise SystemExit(
                f"qualification evidence {record_id!r} passed result cannot contain "
                "failed or skipped checks: "
                + ", ".join(non_passed_recorded)
            )
    elif all(status == "passed" for status in statuses.values()):
        raise SystemExit(
            f"qualification evidence {record_id!r} failed result requires at least "
            "one failed or skipped check"
        )


def _resolve_source_path(
    record_id: str,
    source: dict[str, Any],
    *,
    root: Path,
) -> tuple[Path, Path]:
    relative = Path(str(source["path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(
            f"qualification evidence {record_id!r}.source.path must stay inside the repository"
        )
    resolved = root / relative
    if not resolved.exists():
        raise SystemExit(
            f"qualification evidence {record_id!r}.source.path does not exist: {source['path']}"
        )
    return relative, resolved


def _validate_qualified_run_receipt(
    record_id: str,
    record: dict[str, Any],
    *,
    root: Path,
) -> None:
    source = record["source"]
    relative, receipt_path = _resolve_source_path(record_id, source, root=root)
    try:
        relative.relative_to(_QUALIFICATION_RECEIPT_DIR)
    except ValueError as exc:
        raise SystemExit(
            f"qualification evidence {record_id!r} qualified_run source must live under "
            "evidence/qualification/runs/"
        ) from exc
    if receipt_path.suffix != ".json":
        raise SystemExit(
            f"qualification evidence {record_id!r} qualified_run source must be a JSON receipt"
        )
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"qualification evidence {record_id!r} qualified_run receipt must be valid JSON"
        ) from exc
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"version", "kind", "record"}
        or receipt.get("version") != 1
        or receipt.get("kind") != "qualification_run_receipt"
        or not isinstance(receipt.get("record"), dict)
    ):
        raise SystemExit(
            f"qualification evidence {record_id!r} qualified_run receipt must declare "
            "version=1, kind=qualification_run_receipt, and record"
        )
    expected = {key: value for key, value in record.items() if key != "source"}
    if receipt["record"] != expected:
        raise SystemExit(
            f"qualification evidence {record_id!r} qualified_run receipt does not match catalog record"
        )


def _validate_record(
    record_id: str,
    record: object,
    targets: set[str],
    *,
    known_checks: set[str],
    capability_requirements: dict[str, frozenset[str]],
    root: Path,
) -> dict[str, Any]:
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
    if kind == "legacy_backfill":
        _resolve_source_path(record_id, source, root=root)

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
        _validate_qualified_run_checks(
            record_id,
            record,
            known_checks=known_checks,
            capability_requirements=capability_requirements,
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
        _validate_qualified_run_receipt(record_id, record, root=root)

    return record


def validate_qualification_evidence_document(
    document: object,
    profiles_document: object,
    deployment_targets_document: object,
    qualification_checks_document: object | None = None,
    *,
    root: Path = ROOT,
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

    known_checks, capability_requirements = load_qualification_check_contract(
        qualification_checks_document
    )

    validated_records: list[dict[str, Any]] = []
    for record_id, raw_record in records.items():
        if not _non_empty_string(record_id):
            raise SystemExit("qualification evidence record ids must be non-empty strings")
        validated_records.append(
            _validate_record(
                str(record_id),
                raw_record,
                targets,
                known_checks=known_checks,
                capability_requirements=capability_requirements,
                root=root,
            )
        )

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
        read_yaml("configs/qualification_checks.yaml"),
    )
