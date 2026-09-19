#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validation.governance.common import read_yaml  # noqa: E402
from scripts.validation.governance.qualification import (  # noqa: E402
    validate_qualification_evidence_document,
)


def eligible_qualified_run_ids(
    profile_id: str,
    evidence_document: object,
    profiles_document: object,
    deployment_targets_document: object,
    qualification_checks_document: object,
) -> list[str]:
    """Return current qualified_run evidence eligible for an explicit status promotion."""
    validate_qualification_evidence_document(
        evidence_document,
        profiles_document,
        deployment_targets_document,
        qualification_checks_document,
    )

    if not isinstance(profiles_document, dict):
        raise SystemExit("main_model_profiles.yaml must be a mapping")
    profiles = profiles_document.get("profiles")
    if not isinstance(profiles, dict) or profile_id not in profiles:
        raise SystemExit(f"unknown main model profile {profile_id!r}")
    profile = profiles[profile_id]
    if not isinstance(profile, dict):
        raise SystemExit(f"main model profile {profile_id!r} must be a mapping")

    qualification = profile.get("qualification")
    if not isinstance(qualification, dict) or qualification.get("status") != "unverified":
        raise SystemExit(
            f"main model profile {profile_id!r} must currently be unverified for promotion"
        )

    capabilities = profile.get("capabilities")
    deployed_input = (
        capabilities.get("deployed_input") if isinstance(capabilities, dict) else None
    )
    if not isinstance(deployed_input, list) or not deployed_input:
        raise SystemExit(
            f"main model profile {profile_id!r} must declare deployed_input capabilities"
        )

    if not isinstance(evidence_document, dict):
        raise SystemExit("qualification_evidence.yaml must be a mapping")
    records = evidence_document.get("records")
    if not isinstance(records, dict):
        raise SystemExit("qualification_evidence.yaml must declare records")

    expected_model_id = profile.get("model_id")
    expected_revision = profile.get("revision")
    expected_capabilities = set(str(item) for item in deployed_input)
    matches: list[str] = []
    for record_id, raw_record in records.items():
        if not isinstance(raw_record, dict):
            continue
        subject = raw_record.get("subject")
        if not isinstance(subject, dict):
            continue
        if (
            raw_record.get("kind") == "qualified_run"
            and raw_record.get("result") == "passed"
            and subject.get("profile_id") == profile_id
            and subject.get("model_id") == expected_model_id
            and subject.get("revision") == expected_revision
            and set(str(item) for item in raw_record.get("capabilities", []))
            == expected_capabilities
        ):
            matches.append(str(record_id))

    if not matches:
        raise SystemExit(
            f"main model profile {profile_id!r} has no current qualified_run "
            "eligible for verified promotion"
        )
    return sorted(matches)


def check_profile(profile_id: str) -> dict[str, Any]:
    evidence = read_yaml("configs/qualification_evidence.yaml")
    profiles = read_yaml("configs/main_model_profiles.yaml")
    targets = read_yaml("configs/deployment_targets.yaml")
    checks = read_yaml("configs/qualification_checks.yaml")
    record_ids = eligible_qualified_run_ids(profile_id, evidence, profiles, targets, checks)
    return {"profile_id": profile_id, "eligible": True, "qualified_run_ids": record_ids}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Check ADR-0033 eligibility for an explicit unverified-to-verified "
            "profile promotion."
        )
    )
    parser.add_argument("--profile", required=True, help="Main Model profile id")
    args = parser.parse_args()
    print(json.dumps(check_profile(args.profile), sort_keys=True))


if __name__ == "__main__":
    main()
