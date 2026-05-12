from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import yaml

ROOT = Path(__file__).resolve().parents[2]


def walk_refs(obj: Any) -> Iterator[str]:
    if isinstance(obj, dict):
        if '$ref' in obj:
            yield obj['$ref']
        for value in obj.values():
            yield from walk_refs(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk_refs(value)


def test_openapi_local_refs_exist() -> None:
    for rel in ['specs/openapi.gateway.yaml', 'specs/openapi.risk-adapter.yaml']:
        doc = yaml.safe_load((ROOT / rel).read_text(encoding='utf-8'))
        for ref in walk_refs(doc):
            assert ref.startswith('./')
            assert (ROOT / 'specs' / ref[2:]).exists(), ref


def test_model_list_schema_restricts_logical_ids() -> None:
    import json
    from jsonschema import Draft202012Validator

    schema = json.loads((ROOT / "specs/schemas/model_list_response.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    valid = {
        "object": "list",
        "data": [
            {"id": "local-main", "object": "model", "backend": "main_llm_vllm", "capabilities": ["chat.completions"], "request_parameters": {"temperature": {"type": "number", "min": 0, "max": 2}}},
            {"id": "local-embed", "object": "model", "backend": "embedding_vllm", "capabilities": ["embeddings"], "request_parameters": {"dimensions": {"type": "integer", "enum": [768, 512, 256, 128]}}},
            {"id": "risk-prompt", "object": "model", "backend": "risk_adapter", "capabilities": ["risk.prompt_attack_signal"], "request_parameters": {}, "fixed_parameters": {"max_tokens": 1, "temperature": 0}},
        ],
    }
    validator.validate(valid)
    invalid = dict(valid)
    invalid["data"] = [dict(item) for item in valid["data"]]
    invalid["data"][0]["id"] = "retired-main"
    assert list(validator.iter_errors(invalid))


def test_openapi_operator_endpoints_declare_admin_auth() -> None:
    for rel in ["specs/openapi.gateway.yaml", "specs/openapi.risk-adapter.yaml"]:
        doc = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
        schemes = doc["components"]["securitySchemes"]
        assert "bearerAuth" in schemes
        assert "adminBearerAuth" in schemes
        for path in ["/ready", "/metrics"]:
            operation = doc["paths"][path]["get"]
            assert operation["security"] == [{"adminBearerAuth": []}]
            assert "401" in operation["responses"]


def test_openapi_specs_use_current_version() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    for rel in ["specs/openapi.gateway.yaml", "specs/openapi.risk-adapter.yaml"]:
        doc = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
        assert doc["info"]["version"] == version
