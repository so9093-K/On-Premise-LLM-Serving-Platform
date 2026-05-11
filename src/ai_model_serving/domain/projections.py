from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model_registry import (
        ModelCardProjection,
        ModelContractProjection,
        ModelInventoryRow,
        ModelListSchemaProjection,
        ModelRegistry,
        MonitoringTargetProjection,
        RuntimeService,
        RuntimeValidationMatrixCheck,
        RuntimeValidationTarget,
    )


def capability_values(registry: "ModelRegistry") -> tuple[str, ...]:
    """Return a stable capability enum derived from catalog listings."""
    values = {capability for record in registry.iter_records() for capability in record.capabilities}
    return tuple(sorted(values))


def capability_values_in_catalog_order(registry: "ModelRegistry") -> tuple[str, ...]:
    """Return capability values in first-seen catalog order for schema readability."""
    ordered: list[str] = []
    seen: set[str] = set()
    for record in registry.iter_records():
        for capability in record.capabilities:
            if capability not in seen:
                ordered.append(capability)
                seen.add(capability)
    return tuple(ordered)


def model_list_schema_projection(registry: "ModelRegistry") -> "ModelListSchemaProjection":
    from .model_registry import ModelListSchemaProjection

    return ModelListSchemaProjection(
        model_ids=registry.public_logical_ids(),
        capability_values=capability_values_in_catalog_order(registry),
    )


def iter_runtime_services(registry: "ModelRegistry") -> tuple["RuntimeService", ...]:
    """Return runtime services in model_serving.yaml order with catalog context."""
    from .model_registry import RuntimeService

    records_by_served_name = {record.served_model_name: record for record in registry.iter_records()}
    services: list[RuntimeService] = []
    for service_key, cfg in registry._serving_models().items():
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


def model_contract_projections(registry: "ModelRegistry") -> tuple["ModelContractProjection", ...]:
    """Derive the model contract inventory from catalog runtime metadata."""
    from .model_registry import ModelContractProjection

    projections: list[ModelContractProjection] = []
    for record in registry.iter_records():
        runtime = registry._catalog_models()[record.logical_id].get("runtime", {})
        runtime_endpoint = str(runtime.get("endpoint") or runtime.get("internal_endpoint") or "")
        public_endpoint = str(runtime.get("endpoint") or runtime.get("public_adapter_endpoint") or runtime_endpoint)
        runtime_kind = "vllm+adapter" if public_endpoint != runtime_endpoint else "vllm"
        projections.append(
            ModelContractProjection(
                logical_id=record.logical_id,
                runtime=runtime_kind,
                port=int(runtime.get("port", record.port or 0)),
                public_endpoint=public_endpoint,
                runtime_endpoint=runtime_endpoint,
            )
        )
    return tuple(projections)


def inventory_rows(registry: "ModelRegistry") -> tuple["ModelInventoryRow", ...]:
    """Return operator-facing model inventory rows without role-specific branching."""
    from .model_registry import ModelInventoryRow

    rows: list[ModelInventoryRow] = []
    for record in registry.iter_records():
        cfg = registry._serving_models().get(record.serving_key or "", {})
        control = cfg.get("resource_control", {}) or {}
        request_limits = control.get("request_limits", {}) or {}
        modalities = request_limits.get("input_modalities") or [request_limits.get("input_modality", "")]
        rows.append(
            ModelInventoryRow(
                id=record.logical_id,
                role=record.role,
                upstream_model_id=record.upstream_model_id,
                port=record.port,
                endpoint=record.endpoint_path,
                isolation=str(control.get("isolation", "")),
                input_modalities=tuple(str(item) for item in modalities if item),
                max_images=request_limits.get("max_image_inputs", ""),
                max_image_bytes=request_limits.get("max_image_bytes", ""),
                max_image_pixels=request_limits.get("max_image_pixels", ""),
                max_model_len=request_limits.get("max_model_len", record.max_model_len),
                max_output_tokens=request_limits.get("max_output_tokens", record.max_output_tokens or ""),
                max_concurrency=control.get("admission_control", {}).get("max_concurrency", cfg.get("gateway_max_concurrency", "")),
                gpu_memory_utilization=cfg.get("gpu_memory_utilization", ""),
                lifecycle_state=record.lifecycle_state,
                exposure=record.exposure,
            )
        )
    return tuple(rows)


def model_card_projections(registry: "ModelRegistry") -> tuple["ModelCardProjection", ...]:
    """Return catalog-derived expectations for model card governance."""
    from .model_registry import ModelCardProjection

    projections: list[ModelCardProjection] = []
    for record in registry.iter_records():
        cfg = registry._catalog_models()[record.logical_id]
        runtime = dict(cfg.get("runtime", {}))
        runtime.pop("served_model_name", None)
        projections.append(
            ModelCardProjection(
                logical_id=record.logical_id,
                upstream_model_id=record.upstream_model_id,
                runtime=runtime,
                source_facts=dict(cfg.get("source_facts", {})),
                project_runtime_policy=dict(cfg.get("project_runtime_policy", {})),
                capabilities=record.capabilities,
            )
        )
    return tuple(projections)


def runtime_validation_targets(registry: "ModelRegistry") -> tuple["RuntimeValidationTarget", ...]:
    """Return one validation target per configured runtime service."""
    from .model_registry import RuntimeValidationTarget

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


def monitoring_targets(registry: "ModelRegistry") -> tuple["MonitoringTargetProjection", ...]:
    """Return Prometheus/Grafana model/runtime label expectations."""
    from .model_registry import MonitoringTargetProjection

    return tuple(
        MonitoringTargetProjection(
            service_key=target.service_key,
            logical_id=target.logical_id,
            compose_service_name=target.compose_service_name,
            port=target.port,
        )
        for target in registry.runtime_validation_targets()
    )


def runtime_validation_matrix_checks(registry: "ModelRegistry") -> tuple["RuntimeValidationMatrixCheck", ...]:
    """Derive the runtime validation matrix from the model registry."""
    from .model_registry import RuntimeValidationMatrixCheck

    all_models = registry.public_logical_ids()
    runtime_services = tuple(target.service_key for target in registry.runtime_validation_targets())
    risk_models = tuple(
        record.logical_id
        for record in registry.iter_records()
        if any(capability.startswith("risk.") for capability in record.capabilities)
    )
    return (
        RuntimeValidationMatrixCheck(
            id="gateway-runtime",
            owner="gateway",
            validation="/health, /ready, /v1/models, chat, and embeddings return schema-valid responses.",
            artifact_file="reports/runtime/gateway-runtime.md",
            runtime_validation_required=True,
            operator_action="Inspect Gateway process, upstream URLs, API key configuration, and dependency readiness.",
            models=all_models,
        ),
        RuntimeValidationMatrixCheck(
            id="risk-adapter-runtime",
            owner="risk-adapter",
            validation="Detector and aggregate endpoints return signal-only responses.",
            artifact_file="reports/runtime/risk-adapter-runtime.md",
            runtime_validation_required=True,
            operator_action="Inspect detector model availability, parser output, and forbidden response field checks.",
            models=risk_models,
        ),
        RuntimeValidationMatrixCheck(
            id="vllm-runtime",
            owner="model-runtime",
            validation="All configured vLLM runtimes load and expose /v1/models or compatible readiness.",
            artifact_file="reports/runtime/vllm-runtime.md",
            runtime_validation_required=True,
            operator_action="Inspect model server startup logs, model names, context length settings, and GPU allocation.",
            models=all_models,
            runtime_services=runtime_services,
        ),
        RuntimeValidationMatrixCheck(
            id="gpu-capacity",
            owner="operations",
            validation="Soak run completes without OOM, excessive preemption, or restart loop.",
            artifact_file="reports/runtime/gpu-capacity.md",
            runtime_validation_required=True,
            operator_action="Reduce max_model_len, max_num_seqs, or split models across GPUs when measured headroom is below threshold.",
            runtime_services=runtime_services,
        ),
        RuntimeValidationMatrixCheck(
            id="monitoring-scrape",
            owner="operations",
            validation="Prometheus scrapes Gateway, Risk Adapter, vLLM runtimes, and DCGM exporter.",
            artifact_file="reports/runtime/monitoring-scrape.md",
            runtime_validation_required=True,
            operator_action="Inspect scrape targets, service ports, and exporter availability.",
            runtime_services=runtime_services,
        ),
        RuntimeValidationMatrixCheck(
            id="grafana-dashboard-render",
            owner="operations",
            validation="Reference dashboards render without prompt, user text, or model output labels.",
            artifact_file="reports/runtime/grafana-dashboard-render.md",
            runtime_validation_required=True,
            operator_action="Check Grafana data source binding, dashboard imports, and metric mapping.",
            runtime_services=runtime_services,
        ),
    )


def runtime_validation_matrix_document(registry: "ModelRegistry") -> dict[str, Any]:
    return {
        "version": str(registry.catalog.get("version", "")),
        "version_semantics": "runtime validation matrix schema version, not package release version",
        "validation_policy": "runtime_validation_required",
        "validation_checks": [check.as_yaml_item() for check in registry.runtime_validation_matrix_checks()],
    }
