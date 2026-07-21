from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model_records import ModelRecord, PublicModel, RegistryIssue, RuntimeService
from .projection_models import (
    ModelCardProjection,
    ModelContractProjection,
    ModelInventoryRow,
    ModelListSchemaProjection,
    MonitoringTargetProjection,
    RuntimeValidationMatrixCheck,
    RuntimeValidationTarget,
)
from .request_surfaces import _request_parameter_surface


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
