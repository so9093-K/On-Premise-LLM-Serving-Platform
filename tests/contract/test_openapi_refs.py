"""model list schema가 알려진 logical ID만 허용하는지 검증한다."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

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
            {"id": "local-embed-ko", "object": "model", "backend": "embedding_ko_vllm", "capabilities": ["embeddings", "retrieval_rerank"], "request_parameters": {"dimensions": {"type": "integer", "enum": [1024]}}},
            {"id": "risk-prompt", "object": "model", "backend": "risk_adapter", "capabilities": ["risk.prompt_attack_signal"], "request_parameters": {}, "fixed_parameters": {"max_tokens": 1, "temperature": 0}},
        ],
    }
    validator.validate(valid)
    invalid = dict(valid)
    invalid["data"] = [dict(item) for item in valid["data"]]
    invalid["data"][0]["id"] = "retired-main"
    assert list(validator.iter_errors(invalid))
