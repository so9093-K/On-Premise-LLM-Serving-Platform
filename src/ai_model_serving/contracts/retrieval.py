from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from ..errors import ServiceError
from ..settings import resolve_project_root
from .common import ensure_object


@lru_cache(maxsize=1)
def _schema_dir() -> Path:
    schema_dir = resolve_project_root() / "specs" / "schemas"
    if not schema_dir.exists():
        raise RuntimeError(f"contract schema directory not found: {schema_dir}")
    return schema_dir


@lru_cache(maxsize=None)
def _validator(schema_name: str) -> Draft202012Validator:
    schema = json.loads((_schema_dir() / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_with_schema(payload: Any, schema_name: str, label: str) -> dict[str, Any]:
    payload = ensure_object(payload)
    try:
        _validator(schema_name).validate(payload)
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        location = f" at {path}" if path else ""
        raise ServiceError(
            "VALIDATION_ERROR",
            f"{label} request schema violation{location}: {exc.message}",
            False,
            422,
        ) from exc
    return payload


def validate_retrieval_rerank_request(payload: Any) -> dict[str, Any]:
    return _validate_with_schema(payload, "retrieval_rerank_request.schema.json", "retrieval rerank")


def validate_retrieval_score_request(payload: Any) -> dict[str, Any]:
    return _validate_with_schema(payload, "retrieval_score_request.schema.json", "retrieval score")
