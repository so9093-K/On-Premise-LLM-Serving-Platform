from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse




def _chat_request_parameters(policy: dict[str, Any], *, max_output_tokens: int | None) -> dict[str, dict[str, Any]]:
    """사용자가 Gateway request에서 직접 조정할 수 있는 chat parameter surface."""
    supported = set(policy.get("supported_parameters", []))
    tool_policy = policy.get("tool_calling", {}) if isinstance(policy.get("tool_calling", {}), dict) else {}
    max_tools = int(tool_policy.get("max_tools", 16))
    parallel_tools_enabled = tool_policy.get("allow_parallel_tool_calls") is True
    max_n = int(policy.get("max_n", 1))
    definitions: dict[str, dict[str, Any]] = {
        "temperature": {"type": "number", "min": 0, "max": 2},
        "max_tokens": {"type": "integer", "min": 1, **({"max": int(max_output_tokens)} if max_output_tokens is not None else {})},
        "top_p": {"type": "number", "min_exclusive": 0, "max": 1},
        "top_k": {"type": "integer", "min": -1},
        "min_p": {"type": "number", "min": 0, "max": 1},
        "presence_penalty": {"type": "number", "min": -2, "max": 2},
        "frequency_penalty": {"type": "number", "min": -2, "max": 2},
        "repetition_penalty": {"type": "number", "min_exclusive": 0, "max": 2},
        "stop": {"type": "string_or_string_array", "max_items": 8},
        "seed": {"type": "integer", "min": 0},
        "n": {"type": "integer", "min": 1, "max": max_n},
        "stream": {"type": "boolean"},
        "stream_options": {"type": "object", "properties": {"include_usage": {"type": "boolean"}}, "additional_properties": False},
        "tools": {"type": "array", "min_items": 1, "max_items": max_tools},
        "tool_choice": {"type": "string_or_function_choice", "allowed": ["auto", "none", "required"]},
        "parallel_tool_calls": {"type": "boolean", "const": parallel_tools_enabled},
    }
    return {name: definitions[name] for name in policy.get("supported_parameters", []) if name in definitions and name in supported}


def _embedding_request_parameters(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """사용자가 Gateway request에서 직접 조정할 수 있는 embedding parameter surface."""
    supported = set(policy.get("supported_parameters", []))
    definitions: dict[str, dict[str, Any]] = {
        "dimensions": {"type": "integer", "enum": [int(item) for item in policy.get("dimensions", [])]},
        "encoding_format": {"type": "string", "const": "float"},
        "truncate_prompt_tokens": {
            "type": "integer",
            "one_of": [
                {"const": -1},
                {"min": 1, "max": int(policy.get("max_truncate_prompt_tokens", 2048))},
            ],
        },
    }
    return {name: definitions[name] for name in policy.get("supported_parameters", []) if name in definitions and name in supported}


def _request_parameter_surface(
    *,
    capabilities: tuple[str, ...],
    serving_cfg: dict[str, Any],
    max_output_tokens: int | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return (user-adjustable parameters, runtime-fixed parameters) for model discovery."""
    policy = serving_cfg.get("request_parameter_policy", {}) if isinstance(serving_cfg.get("request_parameter_policy", {}), dict) else {}
    if any(capability.startswith("risk.") for capability in capabilities):
        fixed = policy.get("fixed_parameters", {}) if isinstance(policy.get("fixed_parameters", {}), dict) else {}
        return {}, dict(fixed)
    if any(capability.startswith("chat.completions") for capability in capabilities):
        return _chat_request_parameters(policy, max_output_tokens=max_output_tokens), {}
    if "embeddings" in capabilities:
        return _embedding_request_parameters(policy), {}
    return {}, {}

@dataclass(frozen=True)
class PublicModel:
    """Gateway가 노출하는 OpenAI 호환 model listing projection."""

    id: str
    backend: str
    capabilities: tuple[str, ...]
    request_parameters: dict[str, dict[str, Any]]
    fixed_parameters: dict[str, Any]
    object: str = "model"

    def as_response_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": self.id,
            "object": self.object,
            "backend": self.backend,
            "capabilities": list(self.capabilities),
            "request_parameters": dict(self.request_parameters),
        }
        if self.fixed_parameters:
            item["fixed_parameters"] = dict(self.fixed_parameters)
        return item


@dataclass(frozen=True)
class ModelRecord:
    """logical model 하나에 대한 정규화된 domain view.

    catalog가 source of truth이지만 호출자는 raw YAML dictionary 대신 이 object를 사용한다. ``serving_key``는 runtime deployment stanza가 생기기 전에도 문서 렌더링 같은 catalog-only workflow가 동작해야 하므로 optional이다.
    """

    logical_id: str
    role: str
    upstream_model_id: str
    served_model_name: str
    backend: str
    capabilities: tuple[str, ...]
    request_parameters: dict[str, dict[str, Any]]
    fixed_parameters: dict[str, Any]
    public_enabled: bool
    serving_key: str | None = None
    port: int | None = None
    endpoint_path: str | None = None
    max_model_len: int | None = None
    max_output_tokens: int | None = None
    embedding_dimensions: tuple[int, ...] = ()
    input_modalities: tuple[str, ...] = ()
    output_modalities: tuple[str, ...] = ()
    lifecycle_state: str = "active"
    exposure: str = "public"
    owner: str = "platform"

    def public_model(self) -> PublicModel | None:
        if not self.public_enabled:
            return None
        return PublicModel(
            id=self.logical_id,
            backend=self.backend,
            capabilities=self.capabilities,
            request_parameters=self.request_parameters,
            fixed_parameters=self.fixed_parameters,
        )


@dataclass(frozen=True)
class RuntimeService:
    """설정된 vLLM runtime service 하나에 대한 정규화된 view."""

    service_key: str
    logical_id: str | None
    role: str
    upstream_model_id: str
    served_model_name: str
    port: int
    endpoint_path: str | None
    endpoint_url: str
    backend: str
    config: dict[str, Any]

    @property
    def compose_service_name(self) -> str:
        """설정된 endpoint URL에서 Compose DNS service name을 반환한다."""
        parsed = urlparse(self.endpoint_url)
        return parsed.hostname or ""


@dataclass(frozen=True)
class ModelContractProjection:
    """governance와 documentation tool이 사용하는 파생 model-contract row."""

    logical_id: str
    runtime: str
    port: int
    public_endpoint: str
    runtime_endpoint: str

    def as_yaml_item(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "port": self.port,
            "public_endpoint": self.public_endpoint,
            "runtime_endpoint": self.runtime_endpoint,
        }


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
class ModelCardProjection:
    """Catalog-derived expectation for a model_cards/<logical-id>.json file.

    Model cards may keep a more human-facing ``role`` label than the runtime
    catalog, so this projection focuses on fields that must remain aligned for
    add/remove/update workflows: identity, runtime surface, source facts, and
    project runtime policy.
    """

    logical_id: str
    upstream_model_id: str
    runtime: dict[str, Any]
    source_facts: dict[str, Any]
    project_runtime_policy: dict[str, Any]
    capabilities: tuple[str, ...]

    def comparable_fields(self) -> dict[str, Any]:
        return {
            "logical_id": self.logical_id,
            "upstream_model_id": self.upstream_model_id,
            "runtime": dict(self.runtime),
            "source_facts": dict(self.source_facts),
            "project_runtime_policy": dict(self.project_runtime_policy),
            "capabilities": list(self.capabilities),
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


@dataclass(frozen=True)
class RegistryIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ModelRegistry:
    """Read model catalog data through explicit domain methods.

    This keeps Gateway/API code from depending on the raw YAML layout and gives
    model add/remove workflows one place to validate catalog, serving config,
    model cards, schemas, and runtime harness expectations.
    """

    catalog: dict[str, Any]
    serving_config: dict[str, Any] | None = None

    def _catalog_models(self) -> dict[str, Any]:
        return dict(self.catalog.get("models", {}))

    def _serving_models(self) -> dict[str, Any]:
        if self.serving_config is None:
            return {}
        return dict(self.serving_config.get("models", {}))

    def _serving_by_logical_id(self) -> dict[str, tuple[str, dict[str, Any]]]:
        mapping: dict[str, tuple[str, dict[str, Any]]] = {}
        for key, cfg in self._serving_models().items():
            served_name = str(cfg.get("served_model_name", ""))
            if served_name:
                mapping[served_name] = (str(key), cfg)
        return mapping

    def logical_ids(self) -> tuple[str, ...]:
        return tuple(self._catalog_models().keys())

    def public_logical_ids(self) -> tuple[str, ...]:
        return tuple(record.logical_id for record in self.iter_records() if record.public_enabled)

    def iter_records(self) -> tuple[ModelRecord, ...]:
        serving_by_id = self._serving_by_logical_id()
        records: list[ModelRecord] = []
        for logical_id, cfg in self._catalog_models().items():
            listing = cfg.get("gateway_listing", {})
            runtime = cfg.get("runtime", {})
            policy = cfg.get("project_runtime_policy", {})
            lifecycle = cfg.get("lifecycle", {})
            serving_key, serving_cfg = serving_by_id.get(str(logical_id), (None, {}))
            dimensions = cfg.get("embedding_dimensions", {}).get("matryoshka_supported", [])
            capabilities = listing.get("capabilities") or [cfg.get("primary_capability")]
            backend = str(listing.get("backend", runtime.get("backend", serving_cfg.get("backend", "runtime"))))
            max_model_len = serving_cfg.get("max_model_len", policy.get("max_model_len"))
            max_output_tokens = serving_cfg.get("max_output_tokens", runtime.get("max_output_tokens", policy.get("max_output_tokens")))
            capabilities_tuple = tuple(str(item) for item in capabilities if item)
            request_parameters, fixed_parameters = _request_parameter_surface(
                capabilities=capabilities_tuple,
                serving_cfg=serving_cfg,
                max_output_tokens=int(max_output_tokens) if max_output_tokens is not None else None,
            )
            records.append(
                ModelRecord(
                    logical_id=str(logical_id),
                    role=str(cfg.get("role", "")),
                    upstream_model_id=str(cfg.get("upstream_model_id", "")),
                    served_model_name=str(runtime.get("served_model_name", logical_id)),
                    backend=backend,
                    capabilities=capabilities_tuple,
                    request_parameters=request_parameters,
                    fixed_parameters=fixed_parameters,
                    public_enabled=listing.get("enabled", True) is True,
                    serving_key=serving_key,
                    port=int(serving_cfg.get("port", runtime.get("port"))) if serving_cfg.get("port", runtime.get("port")) is not None else None,
                    endpoint_path=str(runtime.get("endpoint", runtime.get("internal_endpoint", runtime.get("public_adapter_endpoint", ""))) or "") or None,
                    max_model_len=int(max_model_len) if max_model_len is not None else None,
                    max_output_tokens=int(max_output_tokens) if max_output_tokens is not None else None,
                    embedding_dimensions=tuple(int(item) for item in dimensions),
                    input_modalities=tuple(str(item) for item in cfg.get("deployed_modalities", cfg.get("input_modalities", []))),
                    output_modalities=tuple(str(item) for item in cfg.get("output_modalities", [])),
                    lifecycle_state=str(lifecycle.get("state", "active")),
                    exposure=str(lifecycle.get("exposure", "public" if listing.get("enabled", True) is True else "internal")),
                    owner=str(lifecycle.get("owner", "platform")),
                )
            )
        return tuple(records)

    def record(self, logical_id: str) -> ModelRecord:
        for item in self.iter_records():
            if item.logical_id == logical_id:
                return item
        raise KeyError(logical_id)

    def iter_public_models(self) -> tuple[PublicModel, ...]:
        return tuple(model for record in self.iter_records() if (model := record.public_model()) is not None)

    def public_model_response_items(self) -> tuple[dict[str, Any], ...]:
        return tuple(model.as_response_item() for model in self.iter_public_models())

    def capability_values(self) -> tuple[str, ...]:
        from .projections import capability_values

        return capability_values(self)

    def capability_values_in_catalog_order(self) -> tuple[str, ...]:
        from .projections import capability_values_in_catalog_order

        return capability_values_in_catalog_order(self)

    def model_list_schema_projection(self) -> ModelListSchemaProjection:
        from .projections import model_list_schema_projection

        return model_list_schema_projection(self)

    def model_list_schema_document(self) -> dict[str, Any]:
        return self.model_list_schema_projection().as_json_schema()

    def iter_runtime_services(self) -> tuple[RuntimeService, ...]:
        from .projections import iter_runtime_services

        return iter_runtime_services(self)

    def runtime_service(self, service_key: str) -> RuntimeService:
        for service in self.iter_runtime_services():
            if service.service_key == service_key:
                return service
        raise KeyError(service_key)

    def model_contract_projections(self) -> tuple[ModelContractProjection, ...]:
        from .projections import model_contract_projections

        return model_contract_projections(self)

    def model_contracts_document(self) -> dict[str, Any]:
        return {
            "models": {item.logical_id: item.as_yaml_item() for item in self.model_contract_projections()}
        }

    def inventory_rows(self) -> tuple[ModelInventoryRow, ...]:
        from .projections import inventory_rows

        return inventory_rows(self)

    def model_card_projections(self) -> tuple[ModelCardProjection, ...]:
        from .projections import model_card_projections

        return model_card_projections(self)

    def model_card_projection(self, logical_id: str) -> ModelCardProjection:
        for projection in self.model_card_projections():
            if projection.logical_id == logical_id:
                return projection
        raise KeyError(logical_id)

    def runtime_validation_targets(self) -> tuple[RuntimeValidationTarget, ...]:
        from .projections import runtime_validation_targets

        return runtime_validation_targets(self)

    def monitoring_targets(self) -> tuple[MonitoringTargetProjection, ...]:
        from .projections import monitoring_targets

        return monitoring_targets(self)

    def monitoring_model_labels(self) -> tuple[str, ...]:
        return tuple(target.logical_id for target in self.monitoring_targets())

    def monitoring_compose_service_labels(self) -> tuple[str, ...]:
        return tuple(target.compose_service_name for target in self.monitoring_targets())

    def monitoring_compose_service_regex(self) -> str:
        return "|".join(self.monitoring_compose_service_labels())

    def runtime_validation_matrix_checks(self) -> tuple[RuntimeValidationMatrixCheck, ...]:
        from .projections import runtime_validation_matrix_checks

        return runtime_validation_matrix_checks(self)

    def runtime_validation_matrix_document(self) -> dict[str, Any]:
        from .projections import runtime_validation_matrix_document

        return runtime_validation_matrix_document(self)


    def alignment_issues(self) -> tuple[RegistryIssue, ...]:
        """Return non-fatal catalog/serving alignment issues.

        This intentionally does not prescribe deployment security or runtime
        topology.  It only checks that the model registry and model serving
        stanzas describe the same logical models.
        """
        issues: list[RegistryIssue] = []
        catalog_ids = set(self.logical_ids())
        serving_by_id = self._serving_by_logical_id()
        serving_ids = set(serving_by_id)
        for logical_id in sorted(catalog_ids - serving_ids):
            issues.append(RegistryIssue("missing_serving_model", f"{logical_id} exists in model_catalog.yaml but not model_serving.yaml."))
        for logical_id in sorted(serving_ids - catalog_ids):
            issues.append(RegistryIssue("unknown_serving_model", f"{logical_id} exists in model_serving.yaml but not model_catalog.yaml."))
        for record in self.iter_records():
            if record.serving_key is None:
                continue
            serving_cfg = self._serving_models()[record.serving_key]
            catalog_runtime = self._catalog_models()[record.logical_id].get("runtime", {})
            if int(serving_cfg.get("port", -1)) != int(catalog_runtime.get("port", -1)):
                issues.append(RegistryIssue("port_mismatch", f"{record.logical_id} catalog/runtime port disagrees with serving config."))
            if str(serving_cfg.get("name", "")) != record.upstream_model_id:
                issues.append(RegistryIssue("upstream_model_mismatch", f"{record.logical_id} upstream model id disagrees between catalog and serving config."))
        return tuple(issues)
