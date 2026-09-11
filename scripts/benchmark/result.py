"""결과 문서를 검증하고 저장한다(ADR-0026 8·9절).

schema를 통과하지 못한 결과는 쓰지 않는다. 검증하지 않은 파일이 reports에 남으면
나중에 그 파일이 근거로 쓰인다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from scripts.benchmark.contract import RESULT_SCHEMA_PATH, ROOT

REPORTS_DIR = ROOT / "reports" / "performance"


class ResultValidationError(RuntimeError):
    pass


def _validator() -> Draft202012Validator:
    schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate(document: dict[str, Any]) -> None:
    errors = sorted(_validator().iter_errors(document), key=lambda error: list(error.absolute_path))
    if not errors:
        return
    lines = [f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}" for error in errors]
    raise ResultValidationError("result document does not satisfy the schema:\n  " + "\n  ".join(lines))


def write(document: dict[str, Any], *, directory: Path | None = None) -> Path:
    validate(document)
    target_dir = directory or REPORTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{document['run']['id']}.json"
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
