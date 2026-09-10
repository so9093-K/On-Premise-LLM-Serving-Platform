"""실행 애플리케이션에서 배포용 정적 OpenAPI 문서를 생성한다."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

STRICT_ENV = {
    "APP_ENV": "production",
    "AUTH_MODE": "strict",
    "API_KEY_REQUIRED": "true",
    "API_KEYS": "openapi-gateway-fixture",
    "ADMIN_API_KEY_REQUIRED": "true",
    "ADMIN_API_KEYS": "openapi-admin-fixture",
    "INTERNAL_SERVICE_AUTH_REQUIRED": "true",
    "INTERNAL_SERVICE_TOKEN": "openapi-internal-fixture",
    "FASTAPI_DOCS_ENABLED": "true",
    "VLLM_IMAGE": "registry.example.com/vllm-unified@sha256:" + "0" * 64,
    "AUDIO_VLLM_IMAGE": "registry.example.com/vllm-unified@sha256:" + "1" * 64,
}

_GENERATED_HEADER = (
    "# 자동 생성 파일입니다. 직접 수정하지 마세요.\n"
    "# 소스: FastAPI routes + api/endpoint_spec.py + specs/schemas + configs\n"
    "# 명령: make render-runtime-assets\n"
)


def build_generated_openapi() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """고정된 strict-auth fixture로 두 애플리케이션의 OpenAPI를 생성한다."""
    previous = {key: os.environ.get(key) for key in STRICT_ENV}
    os.environ.update(STRICT_ENV)
    try:
        from ai_model_serving.apps.gateway import create_gateway_app
        from ai_model_serving.apps.risk_adapter import create_risk_adapter_app
        from ai_model_serving.settings import load_settings

        settings = load_settings(root=ROOT)
        policies = settings.main_model_profile_policies
        return {
            "gateway": create_gateway_app(settings=settings).openapi(),
            "risk-adapter": create_risk_adapter_app(settings=settings).openapi(),
        }, policies
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _common_error_ref(schema: Any) -> dict[str, Any] | None:
    """인라인 공통 오류 스키마를 외부 ref + endpoint code 제약으로 축약한다."""
    try:
        codes = schema["properties"]["error"]["properties"]["code"]["enum"]
    except (KeyError, TypeError):
        return None
    return {
        "allOf": [
            {"$ref": "./schemas/common_error.schema.json"},
            {
                "type": "object",
                "properties": {
                    "error": {
                        "type": "object",
                        "properties": {"code": {"enum": list(codes)}},
                    }
                },
            },
        ]
    }


def render_static_openapi(document: dict[str, Any]) -> str:
    """runtime 문서를 정적 배포 파일 형태로 축약해 결정적으로 직렬화한다."""
    rendered = copy.deepcopy(document)
    for path_item in rendered.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            if schema_name := operation.get("x-contract-schema"):
                operation["requestBody"]["content"]["application/json"]["schema"] = {
                    "$ref": f"./schemas/{schema_name}"
                }
            if schema_name := operation.get("x-response-contract-schema"):
                operation["responses"]["200"]["content"]["application/json"]["schema"] = {
                    "$ref": f"./schemas/{schema_name}"
                }
            for response in operation.get("responses", {}).values():
                content = response.get("content", {}).get("application/json", {})
                if compact := _common_error_ref(content.get("schema")):
                    content["schema"] = compact
    return _GENERATED_HEADER + yaml.dump(rendered, allow_unicode=True, sort_keys=False)
