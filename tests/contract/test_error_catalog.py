from __future__ import annotations

import importlib.util
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


def test_catalog_entries_have_required_descriptive_fields():
    for code, meta in _catalog().items():
        assert isinstance(meta.get("retryable"), bool), f"{code}: retryable must be bool"
        assert str(meta.get("meaning", "")).strip(), f"{code}: meaning required"
        assert str(meta.get("action", "")).strip(), f"{code}: action required"


def _load_render_module():
    spec = importlib.util.spec_from_file_location(
        "render_runtime_assets", ROOT / "scripts/render_runtime_assets.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_error_reference_doc_is_in_sync_with_catalog():
    module = _load_render_module()
    expected = module.render_error_reference_md()
    actual = (ROOT / "docs/specs/error_reference.md").read_text(encoding="utf-8")
    assert actual == expected, "docs/specs/error_reference.md 가 오래됨 — make render-runtime-assets 실행 필요"


def test_error_reference_includes_user_facing_guidance_sections():
    actual = (ROOT / "docs/specs/error_reference.md").read_text(encoding="utf-8")
    for phrase in (
        "# Chat API 에러 레퍼런스",
        "`/v1/chat/completions`",
        "통합 에러 코드 카탈로그가 아니라 Chat API 사용자 기준 문서다",
        "| `param` | 고쳐야 할 요청 필드. 없을 수 있다. |",
        "## 판단 기준",
        "## 422 구분",
        "같은 `VALIDATION_ERROR`라도 `param`으로 원인을 나눈다",
        "`response_format`, `response_format.json_schema`",
        "`model`, `max_tokens`, `temperature`, `top_p`, `stop`, `stream_options`, `logprobs`, `top_logprobs`, `logit_bias`, `reasoning`",
        "`tools`, `tools[0].function.name`, `tool_choice`, `tool_choice.function.name`, `tool_calls`",
        "## 정상 요청 예시",
        "## 에러 예시",
        "`messages[0].role`",
        "data:image/png;base64",
        '"type": "input_audio"',
        "data:video/mp4;base64",
        '"response_format": { "type": "json_object" }',
        '"stream": true',
        '"tool_choice": "auto"',
        '"param": "response_format"',
        '"param": "messages"',
        '"param": "messages[0].role"',
        '"param": "max_tokens"',
        '"param": "stream_options"',
        '"param": "tool_choice.function.name"',
        '"param": "image_url"',
        '"param": "input_audio"',
        '"param": "video_url"',
        "including base64 media",
        '"code": "UPSTREAM_SCHEMA_ERROR"',
        "STREAM_LIMIT_EXCEEDED",
    ):
        assert phrase in actual
    assert "DETECTOR_DISABLED" not in actual
    assert "DETECTOR_RETIRED" not in actual
    assert "Risk Adapter" not in actual


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
        # Narrowed, not the full catalog dumped onto every response.
        assert len(enum) < len(ERROR_STATUS)
        assert schema.get("title") == "CommonErrorResponse"
