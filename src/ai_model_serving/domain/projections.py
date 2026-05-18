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
    values = {capability for record in registry.iter_records() if record.public_enabled for capability in record.capabilities}
    return tuple(sorted(values))


def capability_values_in_catalog_order(registry: "ModelRegistry") -> tuple[str, ...]:
    """Return capability values in first-seen catalog order for schema readability."""
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


def model_contract_projections(registry: "ModelRegistry") -> tuple["ModelContractProjection", ...]:
    """Derive the model contract inventory from catalog runtime metadata."""
    from .model_registry import ModelContractProjection

    projections: list[ModelContractProjection] = []
    for record in registry.iter_records():
        runtime = registry._catalog_models()[record.logical_id].get("runtime", {})
        runtime_endpoint = str(runtime.get("endpoint") or runtime.get("internal_endpoint") or "")
        public_endpoint = str(runtime.get("endpoint") or runtime.get("public_adapter_endpoint") or runtime_endpoint)
        if runtime.get("production_vllm_native") is True:
            runtime_kind = "vllm_native"
        else:
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
    chat_models = tuple(
        record.logical_id
        for record in registry.iter_records()
        if record.public_enabled and any(capability.startswith("chat.completions") for capability in record.capabilities)
    )
    main_runtime_services = tuple(
        target.service_key
        for target in registry.runtime_validation_targets()
        if any(capability.startswith("chat.completions") for capability in target.capabilities)
    )
    risk_models = tuple(
        record.logical_id
        for record in registry.iter_records()
        if record.public_enabled and any(capability.startswith("risk.") for capability in record.capabilities)
    )
    return (
        RuntimeValidationMatrixCheck(
            id="gateway-runtime",
            owner="gateway",
            validation="/health, /ready, /v1/models, chat, embeddings, retrieval, and risk return schema-valid responses.",
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
            id="response-format-text-canary",
            owner="gateway",
            validation='response_format={"type":"text"} is accepted and returns normal assistant content.',
            artifact_file="reports/runtime/response-format-text-canary.md",
            runtime_validation_required=True,
            operator_action="Inspect Gateway request policy and vLLM OpenAI-compatible chat response_format handling.",
            models=chat_models,
        ),
        RuntimeValidationMatrixCheck(
            id="response-format-json-object-canary",
            owner="gateway",
            validation='response_format={"type":"json_object"} with an explicit JSON instruction returns valid JSON content.',
            artifact_file="reports/runtime/response-format-json-object-canary.md",
            runtime_validation_required=True,
            operator_action="Inspect JSON mode support and ensure prompts include an explicit JSON instruction.",
            models=chat_models,
        ),
        RuntimeValidationMatrixCheck(
            id="response-format-json-schema-canary",
            owner="gateway",
            validation='response_format={"type":"json_schema"} applies constrained decoding and returns schema-valid JSON content.',
            artifact_file="reports/runtime/response-format-json-schema-canary.md",
            runtime_validation_required=True,
            operator_action="Do not hard-code Gateway rejection; lower operator combination policy to reject only if runtime validation stays failed.",
            models=chat_models,
            feature_degraded_on_failure="structured_outputs",
        ),
        RuntimeValidationMatrixCheck(
            id="logprobs-non-stream-canary",
            owner="gateway",
            validation="logprobs=true and top_logprobs within Gateway cap return choices[].logprobs in non-stream responses.",
            artifact_file="reports/runtime/logprobs-non-stream-canary.md",
            runtime_validation_required=True,
            operator_action="Inspect vLLM logprobs support and response size/latency before changing Gateway caps.",
            models=chat_models,
        ),
        RuntimeValidationMatrixCheck(
            id="logprobs-stream-canary",
            owner="gateway",
            validation="stream=true with logprobs=true relays SSE chunks containing upstream logprobs without buffering or rewriting.",
            artifact_file="reports/runtime/logprobs-stream-canary.md",
            runtime_validation_required=True,
            operator_action="Inspect SSE relay and client chunk parsing; do not add Gateway buffering for this check.",
            models=chat_models,
        ),
        RuntimeValidationMatrixCheck(
            id="logit-bias-shape-canary",
            owner="gateway",
            validation="logit_bias object using served model tokenizer token ids is accepted by vLLM runtime.",
            artifact_file="reports/runtime/logit-bias-shape-canary.md",
            runtime_validation_required=True,
            operator_action="Verify token ids against the served model tokenizer and inspect vLLM request acceptance.",
            models=chat_models,
        ),
        RuntimeValidationMatrixCheck(
            id="json-schema-with-tools-canary",
            owner="gateway",
            validation="json_schema response_format with tools is accepted and either emits schema-valid content or valid tool_calls.",
            artifact_file="reports/runtime/json-schema-with-tools-canary.md",
            runtime_validation_required=True,
            operator_action="Use request_parameter_policy.combinations.json_schema_with_tools.mode=reject only when this deployment cannot support the combination.",
            models=chat_models,
            feature_degraded_on_failure="json_schema_with_tools",
        ),
        RuntimeValidationMatrixCheck(
            id="json-schema-with-reasoning-canary",
            owner="gateway",
            validation="json_schema response_format with reasoning=true remains accepted after Gateway reasoning normalization.",
            artifact_file="reports/runtime/json-schema-with-reasoning-canary.md",
            runtime_validation_required=True,
            operator_action="Use request_parameter_policy.combinations.json_schema_with_reasoning.mode=reject only when this deployment cannot support the combination.",
            models=chat_models,
            feature_degraded_on_failure="json_schema_with_reasoning",
        ),
        RuntimeValidationMatrixCheck(
            id="gemma4-reasoning-parser-structured-outputs-canary",
            owner="model-runtime",
            validation="Gemma4 reasoning parser plus structured outputs config enable_in_reasoning=true constrains json_schema output on the reasoning=false/default path and fails if free text bypasses grammar.",
            artifact_file="reports/runtime/gemma4-reasoning-parser-structured-outputs-canary.md",
            runtime_validation_required=True,
            operator_action="Keep Gateway surface compatible; report degradation and adjust runtime structured_outputs or combination policy after canary evidence.",
            models=chat_models,
            runtime_services=main_runtime_services,
            feature_degraded_on_failure="gemma4_reasoning_parser_structured_outputs",
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
