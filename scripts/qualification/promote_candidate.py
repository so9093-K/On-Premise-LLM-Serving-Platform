#!/usr/bin/env python3
"""Review된 qualification candidate를 repository-owned evidence로 승격한다.

이 명령은 live validation을 실행하거나 profile을 verified로 바꾸지 않는다. 사람이
candidate diff를 검토한 뒤 명시적으로 실행하는 durable-evidence promotion 단계다.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
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


class QualificationPromotionError(RuntimeError):
    """Candidate를 durable evidence로 안전하게 승격할 수 없을 때 발생한다."""


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
        receipt_dir = staged_root / "evidence/qualification/runs"
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


def promote_candidate(candidate_path: Path, *, root: Path) -> tuple[str, Path]:
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
    catalog_path = root / "configs/qualification_evidence.yaml"
    catalog = load_yaml_mapping(catalog_path)
    records = catalog.get("records")
    if not isinstance(records, dict):
        raise QualificationPromotionError("qualification evidence catalog records mapping is missing")
    if record_id in records:
        raise QualificationPromotionError(f"qualification evidence record already exists: {record_id}")

    destination = root / "evidence/qualification/runs" / f"{record_id}.json"
    if destination.exists():
        raise QualificationPromotionError(f"qualification receipt already exists: {destination}")

    record = _catalog_record(receipt, record_id)
    staged_catalog = {**catalog, "records": {**records, record_id: record}}
    try:
        _validate_staged(root=root, receipt=receipt, record_id=record_id, catalog=staged_catalog)
    except SystemExit as exc:
        raise QualificationPromotionError(str(exc)) from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate_path, destination)
    rendered = yaml.safe_dump({record_id: record}, sort_keys=False, allow_unicode=True).rstrip()
    indented = "\n".join(f"  {line}" if line else line for line in rendered.splitlines())
    with catalog_path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + indented + "\n")
    return record_id, destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="review된 qualification candidate를 durable receipt/catalog evidence로 승격합니다."
    )
    parser.add_argument("candidate", help="reports/qualification 아래 candidate JSON")
    parser.add_argument("--root", default=str(ROOT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        record_id, destination = promote_candidate(Path(args.candidate), root=Path(args.root))
    except QualificationPromotionError as exc:
        print(f"qualification promotion refused: {exc}", file=sys.stderr)
        return 2
    print(f"promoted record_id={record_id}")
    print(f"receipt={destination}")
    print("review configs/qualification_evidence.yaml and receipt diff before commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
