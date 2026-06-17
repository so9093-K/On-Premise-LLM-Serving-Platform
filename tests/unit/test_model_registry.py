from __future__ import annotations

import yaml

from ai_model_serving.domain import ModelRegistry


def load_yaml(path: str):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_model_registry_normalizes_public_models_and_serving_records():
    registry = ModelRegistry(
        load_yaml("configs/model_catalog.yaml"),
        load_yaml("configs/model_serving.yaml"),
    )

    assert registry.logical_ids() == ("local-main", "local-embed", "local-embed-ko", "risk-prompt")
    assert registry.public_logical_ids() == ("local-main", "local-embed", "local-embed-ko", "risk-prompt")
    assert registry.alignment_issues() == ()

    main = registry.record("local-main")
    assert main.serving_key == "main_llm"
    assert main.port == 9401
    assert main.max_model_len == 20000
    assert main.input_modalities == ("text", "image")
    assert "chat.completions.tools" in main.capabilities

    embedding = registry.record("local-embed")
    assert embedding.embedding_dimensions == (768, 512, 256, 128)
    assert embedding.serving_key == "embedding"
    embedding_ko = registry.record("local-embed-ko")
    assert embedding_ko.embedding_dimensions == (1024,)
    assert embedding_ko.serving_key == "embedding_ko"


def test_model_registry_reports_catalog_serving_drift():
    catalog = {
        "models": {
            "local-main": {
                "role": "main_llm",
                "upstream_model_id": "expected/model",
                "primary_capability": "chat.completions",
                "runtime": {"served_model_name": "local-main", "backend": "vllm", "port": 9401},
            }
        }
    }
    serving = {
        "models": {
            "main_llm": {
                "name": "different/model",
                "served_model_name": "local-main",
                "port": 9409,
            },
            "other": {"name": "unknown/model", "served_model_name": "unknown", "port": 9410},
        }
    }

    issues = registry = ModelRegistry(catalog, serving).alignment_issues()
    codes = {issue.code for issue in issues}
    assert {"unknown_serving_model", "port_mismatch", "upstream_model_mismatch"}.issubset(codes)


def test_model_registry_projects_runtime_services_and_contracts():
    registry = ModelRegistry(
        load_yaml("configs/model_catalog.yaml"),
        load_yaml("configs/model_serving.yaml"),
    )

    services = {item.service_key: item for item in registry.iter_runtime_services()}
    assert list(services) == ["main_llm", "embedding", "embedding_ko", "risk_prompt"]
    assert services["main_llm"].logical_id == "local-main"
    assert services["main_llm"].served_model_name == "local-main"
    assert services["main_llm"].compose_service_name == "main-llm-vllm"
    assert services["main_llm"].config["optimization_level"] == 3
    assert services["embedding"].compose_service_name == "embedding-vllm"
    assert services["embedding_ko"].compose_service_name == "embedding-ko-vllm"
    assert services["risk_prompt"].endpoint_path == "/v1/chat/completions"

    projected_contracts = registry.model_contracts_document()["models"]
    assert projected_contracts["local-main"]["runtime"] == "vllm"
    assert projected_contracts["local-embed-ko"]["runtime"] == "vllm"
    assert projected_contracts["local-embed-ko"]["public_endpoint"] == "/v1/embeddings"
    assert projected_contracts["local-embed-ko"]["runtime_endpoint"] == "/v1/embeddings"
    assert projected_contracts["risk-prompt"]["runtime"] == "vllm+adapter"
    assert projected_contracts["risk-prompt"]["public_endpoint"] == "/v1/risk/detectors/prompt/assessments"
    assert projected_contracts["risk-prompt"]["runtime_endpoint"] == "/v1/chat/completions"


def test_model_registry_projects_operator_inventory_without_role_mapping():
    registry = ModelRegistry(
        load_yaml("configs/model_catalog.yaml"),
        load_yaml("configs/model_serving.yaml"),
    )

    rows = {row.id: row.as_dict() for row in registry.inventory_rows()}
    assert rows["local-main"]["port"] == 9401
    assert rows["local-main"]["input_modalities"] == "text,image"
    assert rows["local-main"]["max_images"] == 1
    assert rows["local-embed"]["max_model_len"] == 2048


def test_model_registry_projects_model_cards_and_runtime_validation_matrix():
    registry = ModelRegistry(
        load_yaml("configs/model_catalog.yaml"),
        load_yaml("configs/model_serving.yaml"),
    )

    card = registry.model_card_projection("local-main")
    assert card.logical_id == "local-main"
    assert card.upstream_model_id == "RedHatAI/gemma-4-26B-A4B-it-FP8-Dynamic"
    assert card.runtime["port"] == 9401
    assert card.source_facts["base_model_id"] == "google/gemma-4-26B-A4B-it"
    assert card.project_runtime_policy["max_model_len"] == 20000
    assert card.project_runtime_policy["optimization_level"] == 3
    assert "chat.completions.tools" in card.capabilities

    targets = {target.service_key: target.as_dict() for target in registry.runtime_validation_targets()}
    assert targets["main_llm"]["logical_id"] == "local-main"
    assert "risk_siren" not in targets

    matrix = {item["id"]: item for item in registry.runtime_validation_matrix_document()["validation_checks"]}
    assert matrix["gateway-runtime"]["models"] == ["local-main", "local-embed", "local-embed-ko", "risk-prompt"]
    assert matrix["vllm-runtime"]["runtime_services"] == ["main_llm", "embedding", "embedding_ko", "risk_prompt"]
    assert matrix["grafana-dashboard-render"]["runtime_services"] == ["main_llm", "embedding", "embedding_ko", "risk_prompt"]


def test_model_registry_projects_openapi_schema_and_monitoring_labels():
    registry = ModelRegistry(
        load_yaml("configs/model_catalog.yaml"),
        load_yaml("configs/model_serving.yaml"),
    )

    schema = registry.model_list_schema_document()
    item_properties = schema["properties"]["data"]["items"]["properties"]
    assert item_properties["id"]["enum"] == ["local-main", "local-embed", "local-embed-ko", "risk-prompt"]
    assert item_properties["capabilities"]["items"]["enum"] == [
        "chat.completions",
        "chat.completions.vision",
        "chat.completions.tools",
        "embeddings",
        "retrieval_rerank",
        "retrieval_score",
        "risk.prompt_attack_signal",
    ]
    assert "request_parameters" in item_properties
    public_models = {item["id"]: item for item in registry.public_model_response_items()}
    assert public_models["local-main"]["request_parameters"]["max_tokens"] == {"type": "integer", "min": 1, "max": 8192}
    assert public_models["local-main"]["request_parameters"]["top_p"] == {"type": "number", "min_exclusive": 0, "max": 1}
    assert public_models["local-main"]["request_parameters"]["n"] == {"type": "integer", "min": 1, "max": 1}
    assert public_models["local-main"]["request_parameters"]["reasoning"] == {"type": "boolean", "default": False, "mode": "request_opt_in"}
    assert public_models["local-embed"]["request_parameters"]["dimensions"] == {"type": "integer", "enum": [768, 512, 256, 128]}
    assert public_models["local-embed-ko"]["request_parameters"]["dimensions"] == {"type": "integer", "enum": [1024]}
    assert "retrieval_rerank" in public_models["local-embed-ko"]["capabilities"]
    assert public_models["risk-prompt"]["request_parameters"] == {}
    assert public_models["risk-prompt"]["fixed_parameters"] == {"max_tokens": 1, "temperature": 0}

    monitoring = [target.as_dict() for target in registry.monitoring_targets()]
    assert monitoring[0] == {
        "service_key": "main_llm",
        "model": "local-main",
        "runtime_service": "main-llm-vllm",
        "port": 9401,
    }
    assert registry.monitoring_compose_service_regex() == "main-llm-vllm|embedding-vllm|embedding-ko-vllm|risk-prompt-vllm"
