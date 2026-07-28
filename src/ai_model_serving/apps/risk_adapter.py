from __future__ import annotations

from typing import Any


from fastapi import Depends, FastAPI

from ..app_kernel import (
    admin_dependencies as build_admin_dependencies,
    create_service_app,
    install_common_middleware,
    install_exception_handlers,
    register_health,
    register_scalar_docs,
)
from ..errors import ServiceError
from ..service_logging import service_logger
from ..metrics import Metrics
from ..openapi_contracts import install_contract_openapi
from ..risk_input import RiskInputPolicy
from ..risk import DetectorSpec
from ..detectors import PIIProtectionDetector, SecretExposureDetector
from ..detectors.protocol import RiskDetector
from ..services.risk_assessment import RiskAssessmentService
from ..security import require_bearer_auth
from ..settings import AppSettings, SecuritySettings, load_settings
from ..upstream import VLLMClient
from ..api_descriptions import RISK_ADAPTER_DESCRIPTION_TEMPLATE, RISK_ADAPTER_TAGS_METADATA
from ..api_examples import AGGREGATE_EXAMPLES, PROMPT_EXAMPLES, PII_EXAMPLES, SECRET_EXAMPLES
from ..api.endpoint_spec import RISK_ADAPTER_ENDPOINTS, schema_maps_from_specs
from ..api.routers.risk_adapter_ops import build_router as _build_ops_router
from ..api.routers.risk_adapter_risk import build_router as _build_risk_router


class RiskClients:
    def __init__(self, settings: AppSettings) -> None:
        self.detectors = {
            detector.key: VLLMClient(settings.runtime(detector.service_key))
            for detector in settings.enabled_risk_detectors()
            if detector.detector_type == "vllm" and detector.service_key
        }
        self.prompt = self.detectors.get("prompt")
        self.settings = settings

    async def close(self) -> None:
        for client in self.detectors.values():
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()


def _build_local_detectors(settings: AppSettings) -> dict[str, RiskDetector]:
    """Instantiate local (in-process) detectors for each configured local detector key."""
    local: dict[str, RiskDetector] = {}
    for detector in settings.enabled_risk_detectors():
        if detector.detector_type != "local":
            continue
        if detector.key == "pii":
            local["pii"] = PIIProtectionDetector()
        elif detector.key == "secret":
            local["secret"] = SecretExposureDetector()
    return local


def _detector_specs(settings: AppSettings) -> dict[str, DetectorSpec]:
    return {
        detector.key: DetectorSpec(
            name=detector.key,
            source_model=detector.source_model,
            family=detector.family,
            allowed_codes=detector.allowed_codes,
            route=detector.route,
            service_key=detector.service_key,
            max_output_tokens=detector.max_output_tokens,
            temperature=detector.temperature,
        )
        for detector in settings.enabled_risk_detectors()
    }


def _ensure_detector_client_map(clients: Any) -> dict[str, Any]:
    if hasattr(clients, "detectors"):
        return dict(clients.detectors)
    detectors = {}
    if getattr(clients, "prompt", None) is not None:
        detectors["prompt"] = clients.prompt
    clients.detectors = detectors
    return detectors


def create_risk_adapter_app(settings: AppSettings | None = None, clients: RiskClients | None = None) -> FastAPI:
    settings = settings or load_settings()
    clients = clients or RiskClients(settings)
    _ensure_detector_client_map(clients)
    local_detectors = _build_local_detectors(settings)
    metrics = Metrics("risk-adapter")
    logger = service_logger("risk-adapter")
    service = RiskAssessmentService(
        clients,
        metrics,
        input_policy=RiskInputPolicy(settings.risk_input_max_chars),
        detector_specs=_detector_specs(settings),
        aggregate_detector_order=settings.aggregate_detector_order,
        local_detectors=local_detectors,
    )
    internal_security = SecuritySettings(
        api_key_required=settings.security.internal_service_auth_required,
        api_keys=frozenset({settings.security.internal_service_token}),
        internal_service_token=settings.security.internal_service_token,
        internal_service_auth_required=settings.security.internal_service_auth_required,
        auth_mode=settings.security.auth_mode,
    )
    auth = require_bearer_auth(internal_security)
    api_dependencies = [Depends(auth)] if settings.security.internal_service_auth_required else []
    admin_dependencies = build_admin_dependencies(settings)

    app = create_service_app(
        title="Risk Adapter",
        version=settings.project_version,
        description=RISK_ADAPTER_DESCRIPTION_TEMPLATE,
        settings=settings,
        tags_metadata=RISK_ADAPTER_TAGS_METADATA,
        lifespan_resources=(clients,),
    )

    install_common_middleware(app, settings=settings, metrics=metrics, logger=logger)

    def validation_reason(exc: ServiceError) -> str:
        return "risk_prompt" if "prompt" in exc.message.lower() else "request"

    install_exception_handlers(app, metrics=metrics, logger=logger, validation_reason=validation_reason)
    register_scalar_docs(app, settings=settings, title="Risk Adapter")
    register_health(app, service="risk-adapter", operation_id="getRiskAdapterHealth")

    app.include_router(_build_ops_router(admin_dependencies, clients, metrics, settings))
    app.include_router(_build_risk_router(api_dependencies, service, settings))

    _request_schemas, _response_schemas = schema_maps_from_specs(RISK_ADAPTER_ENDPOINTS)
    install_contract_openapi(
        app,
        request_schemas=_request_schemas,
        response_schemas=_response_schemas,
        request_examples={
            ("POST", "/v1/risk/detectors/prompt/assessments"): PROMPT_EXAMPLES,
            ("POST", "/v1/risk/detectors/pii/assessments"): PII_EXAMPLES,
            ("POST", "/v1/risk/detectors/secret/assessments"): SECRET_EXAMPLES,
            ("POST", "/v1/risk/assessments"): AGGREGATE_EXAMPLES,
        },
    )

    return app
