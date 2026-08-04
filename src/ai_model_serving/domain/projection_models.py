from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelInventoryRow:
    """script와 report에서 사용하는 운영자-facing inventory projection."""

    id: str
    role: str
    upstream_model_id: str
    port: int | None
    endpoint: str | None
    isolation: str
    input_modalities: tuple[str, ...]
    max_images: int | str
    max_image_bytes: int | str
    max_image_pixels: int | str
    max_model_len: int | str | None
    max_output_tokens: int | str
    max_concurrency: int | str
    gpu_memory_utilization: float | str
    lifecycle_state: str = "active"
    exposure: str = "public"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "upstream_model_id": self.upstream_model_id,
            "port": self.port,
            "endpoint": self.endpoint,
            "isolation": self.isolation,
            "input_modalities": ",".join(self.input_modalities),
            "max_images": self.max_images,
            "max_image_bytes": self.max_image_bytes,
            "max_image_pixels": self.max_image_pixels,
            "max_model_len": self.max_model_len,
            "max_output_tokens": self.max_output_tokens,
            "max_concurrency": self.max_concurrency,
            "gpu_memory_utilization": self.gpu_memory_utilization,
            "lifecycle_state": self.lifecycle_state,
            "exposure": self.exposure,
        }


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
class RuntimeValidationMatrixCheck:
    """Derived runtime-validation matrix row."""

    id: str
    owner: str
    validation: str
    artifact_file: str
    runtime_validation_required: bool
    operator_action: str
    models: tuple[str, ...] = ()
    runtime_services: tuple[str, ...] = ()
    feature_degraded_on_failure: str | None = None

    def as_yaml_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": self.id,
            "owner": self.owner,
            "validation": self.validation,
            "artifact_file": self.artifact_file,
            "runtime_validation_required": self.runtime_validation_required,
            "operator_action": self.operator_action,
        }
        if self.models:
            item["models"] = list(self.models)
        if self.runtime_services:
            item["runtime_services"] = list(self.runtime_services)
        if self.feature_degraded_on_failure:
            item["feature_degraded_on_failure"] = self.feature_degraded_on_failure
        return item


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
                        "required": ["id", "object", "backend", "capabilities", "request_parameters"],
                        "additionalProperties": True,
                        "properties": {
                            "id": {"enum": list(self.model_ids)},
                            "object": {"const": "model"},
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


@dataclass(frozen=True)
class MonitoringTargetProjection:
    """Registry-derived monitoring label contract for one runtime service."""

    service_key: str
    logical_id: str
    compose_service_name: str
    port: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "service_key": self.service_key,
            "model": self.logical_id,
            "runtime_service": self.compose_service_name,
            "port": self.port,
        }
