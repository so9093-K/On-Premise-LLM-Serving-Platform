from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model_records import RuntimeService
    from .model_registry import ModelRegistry
    from .projection_models import (
        ModelListSchemaProjection,
        RuntimeValidationTarget,
    )


def capability_values_in_catalog_order(registry: "ModelRegistry") -> tuple[str, ...]:
    """스키마 가독성을 위해 catalog에서 처음 등장한 순서로 capability 값을 반환한다."""
    ordered: list[str] = []
    seen: set[str] = set()
    for record in registry.iter_records():
        if not record.public_enabled:
            continue
        for capability in record.capabilities:
            if capability not in seen:
                ordered.append(capability)
                seen.add(capability)
    return tuple(ordered)


def model_list_schema_projection(registry: "ModelRegistry") -> "ModelListSchemaProjection":
    from .projection_models import ModelListSchemaProjection

    return ModelListSchemaProjection(
        model_ids=registry.public_logical_ids(),
        capability_values=capability_values_in_catalog_order(registry),
    )


def iter_runtime_services(registry: "ModelRegistry") -> tuple["RuntimeService", ...]:
    """catalog 문맥을 포함해 ``model_serving.yaml`` 순서대로 runtime 서비스를 반환한다."""
    from .model_records import RuntimeService

    records_by_served_name = {record.served_model_name: record for record in registry.iter_records()}
    services: list[RuntimeService] = []
    for service_key, cfg in registry._serving_models().items():
        if cfg.get("enabled", True) is not True:
            continue
        served_model_name = str(cfg.get("served_model_name", ""))
        record = records_by_served_name.get(served_model_name)
        endpoint_path = record.endpoint_path if record is not None else None
        services.append(
            RuntimeService(
                service_key=str(service_key),
                logical_id=record.logical_id if record is not None else None,
                role=record.role if record is not None else "",
                upstream_model_id=str(cfg.get("name", "")),
                served_model_name=served_model_name,
                port=int(cfg["port"]),
                endpoint_path=endpoint_path,
                endpoint_url=str(cfg.get("endpoint", "")),
                backend=str(cfg.get("backend", "vllm")),
                config=dict(cfg),
            )
        )
    return tuple(services)


def runtime_validation_targets(registry: "ModelRegistry") -> tuple["RuntimeValidationTarget", ...]:
    """설정된 runtime 서비스마다 검증 대상 하나를 반환한다."""
    from .projection_models import RuntimeValidationTarget

    records_by_id = {record.logical_id: record for record in registry.iter_records()}
    targets: list[RuntimeValidationTarget] = []
    for service in registry.iter_runtime_services():
        if service.logical_id is None:
            continue
        record = records_by_id[service.logical_id]
        targets.append(
            RuntimeValidationTarget(
                service_key=service.service_key,
                logical_id=service.logical_id,
                served_model_name=service.served_model_name,
                port=service.port,
                compose_service_name=service.compose_service_name,
                endpoint_url=service.endpoint_url,
                capabilities=record.capabilities,
            )
        )
    return tuple(targets)
