#!/usr/bin/env python3
"""FastAPI가 실제로 생성하는 OpenAPI 문서와 specs/openapi.*.yaml(정적 파일)이
어긋났는지 비교한다. path/method/보안/응답 스키마/예시가 코드와 문서 양쪽에서
수동으로 따로 관리되다 벌어지는 drift를 잡는다."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

STRICT_ENV = {
    "APP_ENV": "production",
    "AUTH_MODE": "strict",
    "API_KEY_REQUIRED": "true",
    "API_KEYS": "snapshot-gateway-key",
    "ADMIN_API_KEY_REQUIRED": "true",
    "ADMIN_API_KEYS": "snapshot-admin-key",
    "INTERNAL_SERVICE_AUTH_REQUIRED": "true",
    "INTERNAL_SERVICE_TOKEN": "snapshot-internal-token",
    "FASTAPI_DOCS_ENABLED": "true",
}


def _load_yaml(rel: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def _json_pointer(document: Any, pointer: str) -> Any:
    current = document
    for raw_part in pointer.lstrip("/").split("/") if pointer else []:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[part]
    return current


def _resolve_refs(value: Any) -> Any:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("./"):
            filename, _, pointer = ref[2:].partition("#")
            external = json.loads((ROOT / "specs" / filename).read_text(encoding="utf-8"))
            return _resolve_refs(copy.deepcopy(_json_pointer(external, pointer)))
        return {key: _resolve_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(item) for item in value]
    return value


def _method_set(doc: dict[str, Any], path: str) -> set[str]:
    return {method for method in doc["paths"].get(path, {}) if method.lower() in {"get", "post", "put", "patch", "delete"}}


def _response_schema(operation: dict[str, Any], code: str) -> Any:
    return operation.get("responses", {}).get(code, {}).get("content", {}).get("application/json", {}).get("schema")


def _request_schema(operation: dict[str, Any]) -> Any:
    return operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")


def _contract_schema_name(schema: Any) -> str | None:
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("./schemas/") and "#" not in ref:
            return ref.rsplit("/", 1)[-1]
    return None


def _expected_schema(schema: Any) -> Any:
    name = _contract_schema_name(schema)
    if name is None:
        return None
    from ai_model_serving.openapi_contracts import load_contract_schema

    return load_contract_schema(name, root=ROOT)


def _build_generated_docs() -> dict[str, dict[str, Any]]:
    previous = {key: os.environ.get(key) for key in STRICT_ENV}
    os.environ.update(STRICT_ENV)
    try:
        from ai_model_serving.apps.gateway import create_gateway_app
        from ai_model_serving.apps.risk_adapter import create_risk_adapter_app

        return {
            "gateway": create_gateway_app().openapi(),
            "risk-adapter": create_risk_adapter_app().openapi(),
        }
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _request_examples(operation: dict[str, Any]) -> dict[str, Any]:
    return (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("examples", {})
    )


def _compare_one(name: str, static_rel: str, generated: dict[str, Any]) -> list[str]:
    static = _load_yaml(static_rel)
    issues: list[str] = []

    if static["info"].get("version") != generated["info"].get("version"):
        issues.append(f"{name}: info.version mismatch: static={static['info'].get('version')} generated={generated['info'].get('version')}")

    if static["info"].get("description") != generated["info"].get("description"):
        issues.append(f"{name}: info.description mismatch")

    static_security_schemes = static.get("components", {}).get("securitySchemes", {})
    generated_security_schemes = generated.get("components", {}).get("securitySchemes", {})
    if static_security_schemes != generated_security_schemes:
        issues.append(f"{name}: components.securitySchemes mismatch")

    static_paths = set(static.get("paths", {}))
    generated_paths = set(generated.get("paths", {}))
    if static_paths != generated_paths:
        issues.append(f"{name}: path set mismatch: static_only={sorted(static_paths - generated_paths)} generated_only={sorted(generated_paths - static_paths)}")

    for path in sorted(static_paths & generated_paths):
        static_methods = _method_set(static, path)
        generated_methods = _method_set(generated, path)
        if static_methods != generated_methods:
            issues.append(f"{name} {path}: method mismatch static={sorted(static_methods)} generated={sorted(generated_methods)}")
            continue
        for method in sorted(static_methods):
            static_op = static["paths"][path][method]
            generated_op = generated["paths"][path][method]
            for field in ("summary", "description", "operationId"):
                if static_op.get(field) != generated_op.get(field):
                    issues.append(f"{name} {method.upper()} {path}: {field} mismatch")
            if static_op.get("security") != generated_op.get("security"):
                issues.append(f"{name} {method.upper()} {path}: security mismatch static={static_op.get('security')} generated={generated_op.get('security')}")
            static_statuses = set(str(code) for code in static_op.get("responses", {}))
            generated_statuses = set(str(code) for code in generated_op.get("responses", {}))
            if static_statuses != generated_statuses:
                issues.append(f"{name} {method.upper()} {path}: response status mismatch static={sorted(static_statuses)} generated={sorted(generated_statuses)}")

            static_request = _request_schema(static_op)
            generated_request = _request_schema(generated_op)
            expected_request = _expected_schema(static_request)
            if expected_request is not None and expected_request != generated_request:
                issues.append(f"{name} {method.upper()} {path}: request schema mismatch")

            static_examples = _request_examples(static_op)
            generated_examples = _request_examples(generated_op)
            static_keys = set(static_examples)
            generated_keys = set(generated_examples)
            if static_keys != generated_keys:
                issues.append(
                    f"{name} {method.upper()} {path}: request examples key mismatch:"
                    f" static_only={sorted(static_keys - generated_keys)}"
                    f" generated_only={sorted(generated_keys - static_keys)}"
                )
            else:
                for key in static_keys:
                    if static_examples[key] != generated_examples[key]:
                        issues.append(f"{name} {method.upper()} {path}: request example '{key}' value mismatch")

            static_response_content = static_op.get("responses", {}).get("200", {}).get("content", {})
            generated_response_content = generated_op.get("responses", {}).get("200", {}).get("content", {})
            compare_response_content = static_op.get("x-response-contract-schema") or path == "/v1/chat/completions"
            if compare_response_content and set(static_response_content) != set(generated_response_content):
                issues.append(
                    f"{name} {method.upper()} {path}: 200 response content-type mismatch static={sorted(static_response_content)} generated={sorted(generated_response_content)}"
                )
            static_response = _response_schema(static_op, "200")
            generated_response = _response_schema(generated_op, "200")
            expected_response = _expected_schema(static_response)
            if expected_response is not None and expected_response != generated_response:
                issues.append(f"{name} {method.upper()} {path}: 200 response schema mismatch")
            if compare_response_content:
                for content_type in sorted(set(static_response_content) & set(generated_response_content) - {"application/json"}):
                    if static_response_content[content_type] != generated_response_content[content_type]:
                        issues.append(f"{name} {method.upper()} {path}: {content_type} response content mismatch")
    return issues


def main() -> int:
    generated = _build_generated_docs()
    issues = []
    issues.extend(_compare_one("gateway", "specs/openapi.gateway.yaml", generated["gateway"]))
    issues.extend(_compare_one("risk-adapter", "specs/openapi.risk-adapter.yaml", generated["risk-adapter"]))
    if issues:
        print("OpenAPI snapshot diff: FAIL")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("OpenAPI snapshot diff: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
