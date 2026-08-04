"""공통 에러 스키마의 code enum이 상태 코드별로 좁혀져 있는지 검증한다.

413/422/503/504 같은 각 상태 코드의 응답 스키마에는 그 상태에 실제로 해당하는
code만 들어있어야 한다 -- ERROR_STATUS 전체를 모든 응답에 그대로 노출하면
클라이언트가 실제로는 나올 수 없는 code까지 처리해야 한다고 착각한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.errors import ERROR_STATUS

from tests.unit.gateway.helpers import FakeGatewayClients, settings as gateway_settings

ROOT = Path(__file__).resolve().parents[2]


def _catalog() -> dict[str, dict]:
    data = yaml.safe_load((ROOT / "configs/error_catalog.yaml").read_text(encoding="utf-8"))
    return data["errors"]


def test_catalog_code_set_matches_error_status():
    catalog = set(_catalog())
    status = set(ERROR_STATUS)
    assert catalog == status, (
        "configs/error_catalog.yaml 의 code 집합이 errors.py ERROR_STATUS 와 어긋남: "
        f"catalog_only={sorted(catalog - status)} status_only={sorted(status - catalog)}"
    )


def test_generated_openapi_scopes_error_code_enum_per_status():
    doc = create_gateway_app(gateway_settings(), FakeGatewayClients()).openapi()
    responses = doc["paths"]["/v1/chat/completions"]["post"]["responses"]
    status_codes: dict[str, set[str]] = {}
    for code, status in ERROR_STATUS.items():
        status_codes.setdefault(str(status), set()).add(code)
    for status in ("413", "422", "503", "504"):
        schema = responses[status]["content"]["application/json"]["schema"]
        enum = set(schema["properties"]["error"]["properties"]["code"]["enum"])
        assert enum == status_codes[status], f"{status} enum not scoped: {sorted(enum)}"
        # 좁혀진 목록이어야 한다 — 모든 응답에 전체 카탈로그를 그대로 쏟아부으면 안 된다.
        assert len(enum) < len(ERROR_STATUS)
        assert schema.get("title") == "CommonErrorResponse"
