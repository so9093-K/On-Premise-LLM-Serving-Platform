from __future__ import annotations

from typing import Any

from ai_model_serving.configuration_schema import validate_configuration_schema_document
from ai_model_serving.configuration_plane import CONFIGURATION_PROJECTION_IDS

from .common import read_json, read_yaml


_OPERATOR_DEFAULT_PATHS: dict[str, tuple[str, ...]] = {
    "operational.max_retrieval_documents": ("operational_limits", "max_retrieval_documents"),
    "streaming.max_duration_seconds": ("streaming", "max_duration_seconds"),
    "streaming.max_chunks": ("streaming", "max_chunks"),
    "streaming.max_bytes": ("streaming", "max_bytes"),
}


def _nested_value(document: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = document
    for part in path:
        if not isinstance(value, dict) or part not in value:
            raise SystemExit(
                f"Configuration Plane source path is missing: {'.'.join(path)}"
            )
        value = value[part]
    return value


def _validate_value_against_metadata(item: dict[str, Any], value: Any) -> None:
    key = item["key"]
    value_type = item["type"]
    if value_type == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        raise SystemExit(f"{key} repository default must be an integer")
    if value_type == "number" and (
        isinstance(value, bool) or not isinstance(value, (int, float))
    ):
        raise SystemExit(f"{key} repository default must be numeric")
    minimum = item.get("minimum")
    maximum = item.get("maximum")
    if minimum is not None and value < minimum:
        raise SystemExit(f"{key} repository default is below metadata minimum {minimum}")
    if maximum is not None and value > maximum:
        raise SystemExit(f"{key} repository default exceeds metadata maximum {maximum}")


def validate_configuration_schema() -> None:
    """Validate Configuration Plane metadata plus repository cross-contracts."""

    document = read_yaml("configs/configuration_schema.yaml")
    try:
        items = validate_configuration_schema_document(
            document,
            projection_ids=CONFIGURATION_PROJECTION_IDS,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    by_key = {str(item["key"]): item for item in items}
    model_serving = read_yaml("configs/model_serving.yaml")
    for key, path in _OPERATOR_DEFAULT_PATHS.items():
        item = by_key.get(key)
        if item is None:
            raise SystemExit(f"configuration_schema.yaml is missing operator metadata for {key}")
        _validate_value_against_metadata(item, _nested_value(model_serving, path))

    retrieval_item = by_key["operational.max_retrieval_documents"]
    declared_maximum = retrieval_item.get("maximum")
    if declared_maximum is None:
        raise SystemExit(
            "operational.max_retrieval_documents must declare maximum to match public retrieval contracts"
        )

    for schema_path in (
        "specs/schemas/retrieval_score_request.schema.json",
        "specs/schemas/retrieval_rerank_request.schema.json",
    ):
        request_schema = read_json(schema_path)
        documents = request_schema.get("properties", {}).get("documents", {})
        contract_maximum = documents.get("maxItems") if isinstance(documents, dict) else None
        if contract_maximum != declared_maximum:
            raise SystemExit(
                f"{schema_path} documents.maxItems={contract_maximum!r} must match "
                f"Configuration metadata maximum={declared_maximum!r}"
            )
