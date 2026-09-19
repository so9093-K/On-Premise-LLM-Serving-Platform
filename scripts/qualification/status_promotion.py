#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.qualification.status_eligibility import eligible_qualified_run_ids  # noqa: E402


@dataclass(frozen=True)
class StatusPromotionPlan:
    profile_id: str
    from_status: str
    to_status: str
    qualified_run_ids: list[str]
    profiles_digest: str
    evidence_digest: str
    plan_digest: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_plan(profile_id: str, *, root: Path = ROOT) -> StatusPromotionPlan:
    profiles_path = root / "configs/main_model_profiles.yaml"
    evidence_path = root / "configs/qualification_evidence.yaml"
    profiles = _read_yaml(profiles_path)
    evidence = _read_yaml(evidence_path)
    targets = _read_yaml(root / "configs/deployment_targets.yaml")
    checks = _read_yaml(root / "configs/qualification_checks.yaml")

    record_ids = eligible_qualified_run_ids(profile_id, evidence, profiles, targets, checks)
    payload: dict[str, Any] = {
        "profile_id": profile_id,
        "from_status": "unverified",
        "to_status": "verified",
        "qualified_run_ids": record_ids,
        "profiles_digest": _sha256(profiles_path),
        "evidence_digest": _sha256(evidence_path),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return StatusPromotionPlan(**payload, plan_digest=digest)


def _promoted_profiles_text(text: str, profile_id: str) -> str:
    profile = re.escape(profile_id)
    pattern = re.compile(
        rf"(?ms)^(  {profile}:\n)(?P<body>.*?)(?=^  \S[^\n]*:\n|\Z)"
    )
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"unknown Main Model profile: {profile_id}")

    body = match.group("body")
    qualification = re.compile(
        r"(?m)^(    qualification:\n(?:      .*\n)*?      status: )unverified(\s*(?:#.*)?$)"
    )
    promoted_body, count = qualification.subn(r"\1verified\2", body, count=1)
    if count != 1:
        raise ValueError(
            f"profile {profile_id!r} does not have exactly one unverified qualification status"
        )
    return text[: match.start("body")] + promoted_body + text[match.end("body") :]


def apply_plan(profile_id: str, confirm: str, *, root: Path = ROOT) -> StatusPromotionPlan:
    plan = build_plan(profile_id, root=root)
    if confirm != plan.plan_digest:
        raise ValueError("reviewed promotion plan no longer matches current repository state")

    profiles_path = root / "configs/main_model_profiles.yaml"
    current = profiles_path.read_text(encoding="utf-8")
    promoted = _promoted_profiles_text(current, profile_id)

    fd, temporary = tempfile.mkstemp(prefix=f".{profiles_path.name}.", dir=profiles_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(promoted)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, profiles_path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan or apply an ADR-0033 qualification status promotion."
    )
    parser.add_argument("--profile", required=True, help="Main Model profile id")
    parser.add_argument("--apply", action="store_true", help="apply the reviewed promotion")
    parser.add_argument("--confirm", help="exact reviewed plan_digest required by --apply")
    args = parser.parse_args()

    if args.apply:
        if not args.confirm:
            parser.error("--apply requires --confirm <plan_digest>")
        plan = apply_plan(args.profile, args.confirm)
    else:
        if args.confirm:
            parser.error("--confirm is only valid with --apply")
        plan = build_plan(args.profile)
    print(json.dumps(asdict(plan), sort_keys=True))


if __name__ == "__main__":
    main()
