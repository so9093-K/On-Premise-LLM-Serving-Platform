"""ModelRegistry의 설정 독립적인 projection 규칙을 검증한다.

현재 배포 모델·포트·정책값의 snapshot은 생성 artifact validator가 검사한다. 이 파일은
작은 fixture로 registry의 정규화와 drift 보고가 동작하는지만 확인한다.
"""

from __future__ import annotations

from ai_model_serving.domain import ModelRegistry


def _registry() -> ModelRegistry:
    catalog = {
        "models": {
            "chat": {
                "role": "main_llm",
                "upstream_model_id": "vendor/chat",
                "primary_capability": "chat.completions",
                "input_modalities": ["text"],
                "runtime": {
                    "served_model_name": "chat",
                    "backend": "vllm",
                    "port": 9001,
                    "endpoint": "/v1/chat/completions",
                    "max_output_tokens": 9,
                },
                "gateway_listing": {
                    "enabled": True,
                    "backend": "chat-runtime",
                    "capabilities": ["chat.completions"],
                },
                "lifecycle": {"state": "active", "exposure": "public", "owner": "platform"},
            },
            "embed": {
                "role": "embedding",
                "upstream_model_id": "vendor/embed",
                "primary_capability": "embeddings",
                "runtime": {
                    "served_model_name": "embed",
                    "backend": "vllm",
                    "port": 9002,
                    "endpoint": "/v1/embeddings",
                },
                "embedding_dimensions": {"matryoshka_supported": [64, 32]},
                "gateway_listing": {
                    "enabled": True,
                    "backend": "embed-runtime",
                    "capabilities": ["embeddings"],
                },
                "lifecycle": {"state": "active", "exposure": "public", "owner": "platform"},
            },
        }
    }
    serving = {
        "models": {
            "chat_runtime": {
                "name": "vendor/chat",
                "served_model_name": "chat",
                "backend": "vllm",
                "port": 9001,
                "endpoint": "http://chat-vllm:9001/v1",
                "max_model_len": 128,
                "request_parameter_policy": {"supported_parameters": ["max_tokens"]},
            },
            "embed_runtime": {
                "name": "vendor/embed",
                "served_model_name": "embed",
                "backend": "vllm",
                "port": 9002,
                "endpoint": "http://embed-vllm:9002/v1",
                "max_model_len": 64,
                "request_parameter_policy": {
                    "supported_parameters": ["dimensions"],
                    "dimensions": [64, 32],
                },
            },
        }
    }
    return ModelRegistry(catalog, serving)


def test_model_registry_projects_catalog_to_runtime_and_public_contracts() -> None:
    registry = _registry()

    assert registry.logical_ids() == ("chat", "embed")
    assert registry.public_logical_ids() == ("chat", "embed")
    assert registry.alignment_issues() == ()

    chat = registry.record("chat")
    assert chat.serving_key == "chat_runtime"
    assert chat.port == 9001
    assert chat.max_model_len == 128
    assert chat.input_modalities == ("text",)
    assert registry.record("embed").embedding_dimensions == (64, 32)

    services = {item.service_key: item for item in registry.iter_runtime_services()}
    assert services["chat_runtime"].logical_id == "chat"
    assert services["chat_runtime"].compose_service_name == "chat-vllm"

    schema = registry.model_list_schema_document()
    assert schema["properties"]["data"]["items"]["properties"]["id"]["enum"] == ["chat", "embed"]
    public_models = {item["id"]: item for item in registry.public_model_response_items()}
    assert public_models["chat"]["request_parameters"]["max_tokens"]["max"] == 9
    assert public_models["embed"]["request_parameters"]["dimensions"]["enum"] == [64, 32]

    targets = {target.service_key: target for target in registry.runtime_validation_targets()}
    assert targets["chat_runtime"].compose_service_name == "chat-vllm"
    assert registry.monitoring_compose_service_regex() == "chat-vllm|embed-vllm"


def test_model_registry_reports_catalog_serving_drift() -> None:
    catalog = {
        "models": {
            "chat": {
                "role": "main_llm",
                "upstream_model_id": "expected/model",
                "primary_capability": "chat.completions",
                "runtime": {"served_model_name": "chat", "backend": "vllm", "port": 9001},
            }
        }
    }
    serving = {
        "models": {
            "chat_runtime": {"name": "different/model", "served_model_name": "chat", "port": 9009},
            "other": {"name": "unknown/model", "served_model_name": "unknown", "port": 9010},
        }
    }

    codes = {issue.code for issue in ModelRegistry(catalog, serving).alignment_issues()}
    assert {"unknown_serving_model", "port_mismatch", "upstream_model_mismatch"}.issubset(codes)
