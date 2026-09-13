from __future__ import annotations

from ai_model_serving.configuration_bindings import operator_runtime_bindings
from ai_model_serving.configuration_schema import validate_configuration_schema_document
from ai_model_serving.configuration_plane import CONFIGURATION_PROJECTION_IDS
from ai_model_serving.configuration_values import validate_configuration_value

from .common import read_json, read_yaml


def _nested_value(document: dict, path: tuple[str, ...]):
    value = document
    for part in path:
        if not isinstance(value, dict) or part not in value:
            raise SystemExit(
                f"Configuration Plane source path is missing: {'.'.join(path)}"
            )
        value = value[part]
    return value


def validate_configuration_schema() -> None:
    """Validate Configuration Plane metadata plus repository cross-contracts."""

    document = read_yaml("configs/configuration_schema.yaml")
    try:
        items = validate_configuration_schema_document(
            document,
            projection_ids=CONFIGURATION_PROJECTION_IDS,
        )
        bindings = operator_runtime_bindings(items)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    by_key = {str(item["key"]): item for item in items}
    model_serving = read_yaml("configs/model_serving.yaml")
    for key, binding in bindings.items():
        item = by_key[key]
        if binding.repository_path is None:
            raise SystemExit(f"{key} is missing repository source binding")
        try:
            validate_configuration_value(
                item,
                _nested_value(model_serving, binding.repository_path),
            )
        except ValueError as exc:
            raise SystemExit(f"{key} repository default invalid: {exc}") from exc

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
