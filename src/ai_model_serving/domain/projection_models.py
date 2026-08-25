from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeValidationTarget:
    """Registry-backed model/runtime target used by live validation and docs."""

    service_key: str
    logical_id: str
    served_model_name: str
    port: int
    compose_service_name: str
    endpoint_url: str
    capabilities: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "service_key": self.service_key,
            "logical_id": self.logical_id,
            "served_model_name": self.served_model_name,
            "port": self.port,
            "compose_service_name": self.compose_service_name,
            "endpoint_url": self.endpoint_url,
            "capabilities": list(self.capabilities),
        }

@dataclass(frozen=True)
class ModelListSchemaProjection:
    """OpenAPI/JSON-schema projection for the Gateway /v1/models response."""

    model_ids: tuple[str, ...]
    capability_values: tuple[str, ...]

    def as_json_schema(self) -> dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "GatewayModelListResponse",
            "type": "object",
            "required": ["object", "data"],
            "additionalProperties": False,
            "properties": {
                "object": {"const": "list"},
                "data": {
                    "type": "array",
                    "minItems": len(self.model_ids),
                    "items": {
                        "type": "object",
                        "required": [
                            "id",
                            "object",
                            "created",
                            "owned_by",
                            "backend",
                            "capabilities",
                            "request_parameters",
                        ],
                        "additionalProperties": True,
                        "properties": {
                            "id": {"enum": list(self.model_ids)},
                            "object": {"const": "model"},
                            "created": {
                                "type": "integer",
                                "minimum": 0,
                                "description": "OpenAI 호환 model object의 생성 시각(unix seconds)입니다. Gateway 프로세스 기동 시각이며 한 프로세스 안에서는 고정입니다.",
                            },
                            "owned_by": {
                                "type": "string",
                                "minLength": 1,
                                "description": "논리 모델을 이 계약으로 서빙하는 주체입니다. 업스트림 가중치의 원저작자가 아닙니다.",
                            },
                            "backend": {"type": "string", "minLength": 1},
                            "capabilities": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"enum": list(self.capability_values)},
                            },
                            "input_modalities": {
                                "type": "array",
                                "description": "현재 활성 모델 프로파일이 입력으로 허용하는 content 종류입니다. chat 요청 검증이 강제하는 집합과 동일하며, 프로파일 전환에 따라 달라질 수 있습니다.",
                                "items": {"type": "string"},
                            },
                            "request_parameters": {
                                "type": "object",
                                "description": "모델별 사용자 조정 가능 request parameter와 제약 조건입니다. prompt/messages/input 같은 필수 입력 본문은 여기에 포함하지 않습니다.",
                                "additionalProperties": {
                                    "type": "object",
                                    "additionalProperties": True,
                                },
                            },
                            "fixed_parameters": {
                                "type": "object",
                                "description": "사용자 요청에는 노출하지 않고 adapter/runtime이 고정하는 내부 parameter입니다.",
                                "additionalProperties": True,
                            },
                        },
                    },
                },
            },
        }
