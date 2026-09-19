#!/usr/bin/env python3
"""Review된 qualification candidate를 repository-owned evidence로 승격한다.

이 명령은 live validation을 실행하거나 profile을 verified로 바꾸지 않는다. 사람이
candidate diff를 검토한 뒤 plan digest를 확인하고 명시적으로 apply하는 durable-evidence
promotion 단계다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ai_model_serving.configuration import load_yaml_mapping  # noqa: E402
from scripts.qualification.produce_candidate import (  # noqa: E402
    QualificationCandidateError,
    candidate_record_id,
)
from scripts.validation.governance.qualification import (  # noqa: E402
    validate_qualification_evidence_document,
)

_CATALOG = Path("configs/qualification_evidence.yaml")
_RECEIPT_DIR = Path("evidence/qualification/runs")


class QualificationPromotionError(RuntimeError):
    """Candidate를 durable evidence로 안전하게 승격할 수 없을 때 발생한다."""


@dataclass(frozen=True)
class PromotionPlan:
    record_id: str
    candidate_path: Path
    destination: Path
    receipt: dict[str, Any]
    catalog_record: dict[str, Any]
    catalog_text: str
    plan_digest: str
    orphan_receipt: bool


def load_candidate(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationPromotionError(f"candidate must be readable JSON: {path}") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "kind", "record"}
        or payload.get("version") != 1
        or payload.get("kind") != "qualification_run_receipt"
        or not isinstance(payload.get("record"), dict)
    ):
        raise QualificationPromotionError(
            "candidate must declare version=1, kind=qualification_run_receipt, and record"
        )
    if payload["record"].get("kind") != "qualified_run":
        raise QualificationPromotionError("candidate record.kind must be qualified_run")
    if payload["record"].get("result") != "passed":
        raise QualificationPromotionError(
            "only a passed qualified_run candidate may be promoted as positive evidence"
        )
    if "source" in payload["record"]:
        raise QualificationPromotionError(
            "candidate record must not contain source; promotion owns durable source"
        )
    try:
        record_id = candidate_record_id(payload)
    except QualificationCandidateError as exc:
        raise QualificationPromotionError(str(exc)) from exc
    if path.stem != record_id:
        raise QualificationPromotionError(
            f"candidate filename must match deterministic record id {record_id!r}"
        )
    return payload


def _catalog_record(receipt: dict[str, Any], record_id: str) -> dict[str, Any]:
    record = dict(receipt["record"])
    record["source"] = {
        "path": f"evidence/qualification/runs/{record_id}.json",
        "note": "reviewed live qualification receipt",
    }
    return record


def _stage_existing_sources(root: Path, staged_root: Path, catalog: dict[str, Any]) -> None:
    records = catalog.get("records", {})
    if not isinstance(records, dict):
        return
    for raw_record in records.values():
        if not isinstance(raw_record, dict):
            continue
        source = raw_record.get("source")
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            continue
        relative = Path(source["path"])
        source_path = root / relative
        if not source_path.is_file():
            continue
        destination = staged_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination)


def _validate_staged(
    *, root: Path, receipt: dict[str, Any], record_id: str, catalog: dict[str, Any]
) -> None:
    with tempfile.TemporaryDirectory(prefix="qualification-promotion-") as raw_tmp:
        staged_root = Path(raw_tmp)
        _stage_existing_sources(root, staged_root, catalog)
        receipt_dir = staged_root / _RECEIPT_DIR
        receipt_dir.mkdir(parents=True, exist_ok=True)
        (receipt_dir / f"{record_id}.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        validate_qualification_evidence_document(
            catalog,
            load_yaml_mapping(root / "configs/main_model_profiles.yaml"),
            load_yaml_mapping(root / "configs/deployment_targets.yaml"),
            load_yaml_mapping(root / "configs/qualification_checks.yaml"),
            root=staged_root,
        )


def _load_catalog(root: Path) -> tuple[dict[str, Any], str, str]:
    path = root / _CATALOG
    try:
        text = path.read_text(encoding="utf-8")
        catalog = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        raise QualificationPromotionError(
            f"qualification evidence catalog must be readable YAML: {path}"
        ) from exc
    if (
        not isinstance(catalog, dict)
        or set(catalog) != {"version", "records"}
        or catalog.get("version") != 1
        or not isinstance(catalog.get("records"), dict)
    ):
        raise QualificationPromotionError(
            "qualification evidence catalog must contain only version: 1 and records mapping"
        )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return catalog, text, digest


def _render_catalog_append(
    original_text: str,
    *,
    record_id: str,
    record: dict[str, Any],
) -> str:
    rendered = yaml.safe_dump(
        {record_id: record},
        sort_keys=False,
        allow_unicode=True,
        width=1000,
    ).rstrip()
    indented = "\n".join(f"  {line}" if line else line for line in rendered.splitlines())
    return original_text.rstrip() + "\n\n" + indented + "\n"


def _plan_digest(
    *,
    record_id: str,
    receipt: dict[str, Any],
    catalog_record: dict[str, Any],
    catalog_before_digest: str,
    orphan_receipt: bool,
) -> str:
    payload = {
        "record_id": record_id,
        "receipt": receipt,
        "catalog_record": catalog_record,
        "catalog_before_sha256": catalog_before_digest,
        "orphan_receipt": orphan_receipt,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def plan_candidate(candidate_path: Path, *, root: Path) -> PromotionPlan:
    candidate_path = candidate_path.resolve()
    root = root.resolve()
    candidate_root = (root / "reports/qualification").resolve()
    try:
        candidate_path.relative_to(candidate_root)
    except ValueError as exc:
        raise QualificationPromotionError(
            "promotion input must be a reviewable candidate under reports/qualification"
        ) from exc

    receipt = load_candidate(candidate_path)
    record_id = candidate_record_id(receipt)
    catalog, catalog_text, catalog_before_digest = _load_catalog(root)
    records = catalog["records"]
    if record_id in records:
        raise QualificationPromotionError(f"qualification evidence record already exists: {record_id}")

    destination = root / _RECEIPT_DIR / f"{record_id}.json"
    orphan_receipt = False
    if destination.exists():
        try:
            existing_receipt = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QualificationPromotionError(
                f"qualification receipt already exists but is unreadable: {destination}"
            ) from exc
        if existing_receipt != receipt:
            raise QualificationPromotionError(
                f"qualification receipt already exists with different content: {destination}"
            )
        orphan_receipt = True

    record = _catalog_record(receipt, record_id)
    staged_catalog = {**catalog, "records": {**records, record_id: record}}
    try:
        _validate_staged(root=root, receipt=receipt, record_id=record_id, catalog=staged_catalog)
    except SystemExit as exc:
        raise QualificationPromotionError(str(exc)) from exc

    rendered_catalog = _render_catalog_append(
        catalog_text,
        record_id=record_id,
        record=record,
    )
    digest = _plan_digest(
        record_id=record_id,
        receipt=receipt,
        catalog_record=record,
        catalog_before_digest=catalog_before_digest,
        orphan_receipt=orphan_receipt,
    )
    return PromotionPlan(
        record_id=record_id,
        candidate_path=candidate_path,
        destination=destination,
        receipt=receipt,
        catalog_record=record,
        catalog_text=rendered_catalog,
        plan_digest=digest,
        orphan_receipt=orphan_receipt,
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def apply_promotion(
    plan: PromotionPlan,
    *,
    root: Path,
    confirm_digest: str,
) -> tuple[str, Path]:
    root = root.resolve()
    fresh = plan_candidate(plan.candidate_path, root=root)
    if fresh.plan_digest != plan.plan_digest:
        raise QualificationPromotionError(
            "promotion plan changed since review; generate a new plan and confirm its digest"
        )
    if confirm_digest != fresh.plan_digest:
        raise QualificationPromotionError(
            "confirmation digest must exactly match the reviewed promotion plan digest"
        )

    catalog_path = root / _CATALOG
    receipt_written = False
    try:
        if not fresh.orphan_receipt:
            _atomic_write(
                fresh.destination,
                json.dumps(fresh.receipt, ensure_ascii=False, indent=2) + "\n",
            )
            receipt_written = True
        _atomic_write(catalog_path, fresh.catalog_text)
    except OSError as exc:
        if receipt_written:
            fresh.destination.unlink(missing_ok=True)
        raise QualificationPromotionError(
            f"failed to apply qualification promotion: {exc}"
        ) from exc
    return fresh.record_id, fresh.destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "review된 qualification candidate의 durable evidence 승격 plan을 생성합니다. "
            "--apply에는 plan digest 확인이 필요합니다."
        )
    )
    parser.add_argument("candidate", help="reports/qualification 아래 candidate JSON")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm",
        default="",
        help="--apply 시 직전 review한 plan_digest와 정확히 같아야 합니다.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    try:
        plan = plan_candidate(Path(args.candidate), root=root)
    except QualificationPromotionError as exc:
        print(f"qualification promotion refused: {exc}", file=sys.stderr)
        return 2

    print(f"record_id={plan.record_id}")
    print(f"plan_digest={plan.plan_digest}")
    print(f"receipt={plan.destination}")
    print(f"catalog={root.resolve() / _CATALOG}")
    if plan.orphan_receipt:
        print("note=matching orphan receipt exists; apply will repair catalog only")

    if not args.apply:
        print("plan only; repository evidence was not modified")
        print(
            "apply with: --apply --confirm "
            f"{plan.plan_digest}"
        )
        return 0

    try:
        record_id, destination = apply_promotion(
            plan,
            root=root,
            confirm_digest=args.confirm,
        )
    except QualificationPromotionError as exc:
        print(f"qualification promotion refused: {exc}", file=sys.stderr)
        return 2

    print(f"promoted record_id={record_id}")
    print(f"receipt={destination}")
    print("profile qualification.status was not changed")
    print("review configs/qualification_evidence.yaml and receipt diff before commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
