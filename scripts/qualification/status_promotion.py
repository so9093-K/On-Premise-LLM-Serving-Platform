#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.qualification.status_eligibility import eligible_qualified_run_ids  # noqa: E402
from scripts.validation.governance.common import read_yaml  # noqa: E402


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


def build_plan(profile_id: str, *, root: Path = ROOT) -> StatusPromotionPlan:
    profiles_path = root / "configs/main_model_profiles.yaml"
    evidence_path = root / "configs/qualification_evidence.yaml"
    profiles = read_yaml(profiles_path.relative_to(root)) if root == ROOT else __import__("yaml").safe_load(profiles_path.read_text(encoding="utf-8"))
    evidence = read_yaml(evidence_path.relative_to(root)) if root == ROOT else __import__("yaml").safe_load(evidence_path.read_text(encoding="utf-8"))
    if root == ROOT:
        targets = read_yaml("configs/deployment_targets.yaml")
        checks = read_yaml("configs/qualification_checks.yaml")
    else:
        import yaml
        targets = yaml.safe_load((root / "configs/deployment_targets.yaml").read_text(encoding="utf-8"))
        checks = yaml.safe_load((root / "configs/qualification_checks.yaml").read_text(encoding="utf-8"))

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan an ADR-0033 qualification status promotion without mutating repository state."
    )
    parser.add_argument("--profile", required=True, help="Main Model profile id")
    args = parser.parse_args()
    print(json.dumps(asdict(build_plan(args.profile)), sort_keys=True))


if __name__ == "__main__":
    main()
