"""결과 문서를 검증하고 저장한다(ADR-0026 8·9절).

schema를 통과하지 못한 결과는 쓰지 않는다. 검증하지 않은 파일이 reports에 남으면
나중에 그 파일이 근거로 쓰인다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from scripts.benchmark.contract import RESULT_SCHEMA_PATH, ROOT, SWEEP_SCHEMA_PATH

REPORTS_DIR = ROOT / "reports" / "performance"


class ResultValidationError(RuntimeError):
    pass


def _validator(schema_path: Path) -> Draft202012Validator:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate(document: dict[str, Any], schema_path: Path = RESULT_SCHEMA_PATH) -> None:
    errors = sorted(_validator(schema_path).iter_errors(document), key=lambda error: list(error.absolute_path))
    if not errors:
        return
    lines = [f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}" for error in errors]
    raise ResultValidationError("result document does not satisfy the schema:\n  " + "\n  ".join(lines))


def write(document: dict[str, Any], *, directory: Path | None = None) -> Path:
    validate(document)
    return _write(document, f"{document['run']['id']}.json", directory)


def write_sweep(document: dict[str, Any], *, directory: Path | None = None) -> Path:
    """용량 결과도 검증 없이 쓰지 않는다.

    7분을 들여 얻은 숫자가 화면에만 나오고 사라지면, SLO의 기준선이 "어느 부하에서
    잰 값"인지 가리킬 근거가 산문 말고는 남지 않는다.
    """
    validate(document, SWEEP_SCHEMA_PATH)
    return _write(document, f"{document['sweep_id']}.sweep.json", directory)


def _write(document: dict[str, Any], name: str, directory: Path | None) -> Path:
    target_dir = directory or REPORTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / name
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
