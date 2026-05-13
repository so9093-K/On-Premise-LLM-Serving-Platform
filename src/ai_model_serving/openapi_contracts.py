from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI

ContractRouteKey = tuple[str, str]
SchemaMap = Mapping[ContractRouteKey, str]
ExamplesMap = Mapping[ContractRouteKey, dict[str, Any]]


STANDARD_ERROR_DESCRIPTIONS: dict[str, str] = {
    "401": "인증 실패",
    "413": "요청 body가 너무 큼",
    "422": "요청 검증 실패",
    "429": "Upstream rate limit 또는 local admission timeout",
    "500": "내부 서버 오류",
    "502": "Upstream 오류 또는 유효하지 않은 upstream 응답",
    "503": "Runtime 또는 dependency를 사용할 수 없음",
    "504": "Upstream timeout",
}

POST_STANDARD_ERROR_CODES = ("401", "413", "422", "429", "500", "502", "503", "504")


def _json_error_response(description: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": copy.deepcopy(schema)}},
    }


def _inject_standard_error_responses(document: dict[str, Any], common_error_schema: dict[str, Any]) -> None:
    """Expose the same error surface in generated OpenAPI as in checked-in specs.

    FastAPI's default generated OpenAPI only documents route-local 200/422
    responses.  The platform runtime maps auth failures, request-size rejection,
    upstream timeouts, circuit-breaker/admission errors, and uncaught exceptions to
    the checked-in ``common_error`` contract.  Injecting those responses here keeps
    ``/docs`` from being weaker than ``specs/openapi.*.yaml``.
    """
    for path_item in document.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})
            codes: list[str] = []
            if method.lower() == "post":
                codes.extend(POST_STANDARD_ERROR_CODES)
            elif operation.get("security"):
                codes.append("401")
            for code in codes:
                if code == "401" and not operation.get("security"):
                    continue
                response = responses.setdefault(
                    code,
                    {"description": STANDARD_ERROR_DESCRIPTIONS[code]},
                )
                response["description"] = response.get("description") or STANDARD_ERROR_DESCRIPTIONS[code]
                content = response.setdefault("content", {}).setdefault("application/json", {})
                content["schema"] = copy.deepcopy(common_error_schema)
            # Inject schema for any 410 responses that are explicitly declared (e.g. retired endpoints).
            if "410" in responses and "content" not in responses["410"]:
                content = responses["410"].setdefault("content", {}).setdefault("application/json", {})
                content["schema"] = copy.deepcopy(common_error_schema)

def find_project_root(start: Path | None = None) -> Path:
    """Locate the repository/config root that contains the JSON contract schemas."""
    candidates: list[Path] = []
    if start is not None:
        candidates.append(start)
    candidates.extend(Path(__file__).resolve().parents)
    candidates.append(Path.cwd())
    for candidate in candidates:
        root = candidate.resolve()
        if (root / "specs" / "schemas").exists() and (root / "VERSION").exists():
            return root
    raise RuntimeError("could not locate project root for OpenAPI contract schemas")


def load_contract_schema(schema_name: str, *, root: Path | None = None) -> dict[str, Any]:
    """Load a JSON schema and inline external file references for generated OpenAPI.

    The checked-in contract schemas are the source of truth for runtime request and
    response validation.  FastAPI routes intentionally accept ``dict[str, Any]`` so
    the app can apply the platform's own contract validators and error mapping.
    Without this helper, generated OpenAPI falls back to a very loose ``object``
    schema.  Loading the same checked-in schemas here keeps ``/docs`` aligned with
    the runtime contract without changing request handling semantics.
    """
    project_root = find_project_root(root)
    schema_dir = project_root / "specs" / "schemas"
    schema = json.loads((schema_dir / schema_name).read_text(encoding="utf-8"))
    external_resolved = _resolve_external_refs(schema, schema_dir=schema_dir)
    return _resolve_internal_refs(external_resolved, root=external_resolved)


def _json_pointer(document: Any, pointer: str) -> Any:
    current = document
    for raw_part in pointer.lstrip("/").split("/") if pointer else []:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[part]
    return current


def _resolve_external_refs(value: Any, *, schema_dir: Path) -> Any:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            filename, _, pointer = ref.partition("#")
            external = json.loads((schema_dir / filename).read_text(encoding="utf-8"))
            resolved = copy.deepcopy(_json_pointer(external, pointer))
            return _resolve_external_refs(resolved, schema_dir=schema_dir)
        return {key: _resolve_external_refs(item, schema_dir=schema_dir) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_external_refs(item, schema_dir=schema_dir) for item in value]
    return value


def _resolve_internal_refs(value: Any, *, root: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#"):
            resolved = copy.deepcopy(_json_pointer(root, ref[1:]))
            return _resolve_internal_refs(resolved, root=root)
        return {key: _resolve_internal_refs(item, root=root) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_internal_refs(item, root=root) for item in value]
    return value


def install_contract_openapi(
    app: FastAPI,
    *,
    request_schemas: SchemaMap | None = None,
    response_schemas: SchemaMap | None = None,
    request_examples: ExamplesMap | None = None,
    root: Path | None = None,
) -> None:
    """Patch a FastAPI app's generated OpenAPI with checked-in contract schemas."""
    request_schemas = request_schemas or {}
    response_schemas = response_schemas or {}
    request_examples = request_examples or {}
    original_openapi = app.openapi
    schema_cache: dict[str, dict[str, Any]] = {}

    def schema_for(schema_name: str) -> dict[str, Any]:
        if schema_name not in schema_cache:
            schema_cache[schema_name] = load_contract_schema(schema_name, root=root)
        return copy.deepcopy(schema_cache[schema_name])

    def contract_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        document = original_openapi()
        paths = document.setdefault("paths", {})
        for (method, path), schema_name in request_schemas.items():
            operation = paths.get(path, {}).get(method.lower())
            if not isinstance(operation, dict):
                continue
            content = operation.setdefault("requestBody", {}).setdefault("content", {}).setdefault("application/json", {})
            content["schema"] = schema_for(schema_name)
            if examples := request_examples.get((method, path)):
                content["examples"] = copy.deepcopy(examples)
            operation["requestBody"]["required"] = True
            operation.setdefault("x-contract-schema", schema_name)
        for (method, path), schema_name in response_schemas.items():
            operation = paths.get(path, {}).get(method.lower())
            if not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})
            response = responses.setdefault("200", {"description": "성공 응답"})
            if path == "/v1/chat/completions" and method.upper() == "POST":
                response["description"] = "Main LLM runtime에서 반환한 OpenAI 호환 chat completion 응답. stream=true일 때는 text/event-stream SSE 응답을 반환한다."
            content = response.setdefault("content", {}).setdefault("application/json", {})
            content["schema"] = schema_for(schema_name)
            if path == "/v1/chat/completions" and method.upper() == "POST":
                stream_content = response.setdefault("content", {}).setdefault("text/event-stream", {})
                stream_content.setdefault(
                    "schema",
                    {
                        "type": "string",
                        "description": "OpenAI-compatible SSE stream. On streaming transport failures, Gateway emits an SSE error event followed by data: [DONE].",
                    },
                )
            operation.setdefault("x-response-contract-schema", schema_name)
        _inject_standard_error_responses(document, schema_for("common_error.schema.json"))
        schemas = document.get("components", {}).get("schemas")
        if isinstance(schemas, dict):
            schemas.pop("HTTPValidationError", None)
            schemas.pop("ValidationError", None)
        app.openapi_schema = document
        return document

    app.openapi = contract_openapi
