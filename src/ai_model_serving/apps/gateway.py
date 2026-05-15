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
from ..logging_policy import service_logger
from ..metrics import Metrics
from ..openapi_contracts import install_contract_openapi
from ..security import require_bearer_auth
from ..settings import AppSettings, RuntimeEndpoint, load_settings
from ..services.gateway_service import GatewayService
from ..upstream import VLLMClient
from ..api_descriptions import GATEWAY_DESCRIPTION_TEMPLATE, GATEWAY_TAGS_METADATA
from ..api_examples import (
    GATEWAY_CHAT_REQUEST_EXAMPLES,
    GATEWAY_EMBEDDING_REQUEST_EXAMPLES,
    GATEWAY_RISK_AGGREGATE_REQUEST_EXAMPLES,
    GATEWAY_RISK_PROMPT_REQUEST_EXAMPLES,
)
from ..api.endpoint_spec import GATEWAY_ENDPOINTS, schema_maps_from_specs
from ..api.routers.gateway_ops import build_router as _build_ops_router
from ..api.routers.gateway_inference import build_router as _build_inference_router
from ..api.routers.gateway_risk import build_router as _build_risk_router

# TODO(playground): /playground 구현 시 /v1/models[].request_parameters를 읽어
# model-aware form을 동적으로 구성한다. 아래 grouping을 참고한다:
# - Basic generation: max_tokens, temperature, top_p
# - Advanced sampling: top_k, min_p, presence_penalty, frequency_penalty, repetition_penalty, seed, n
# - Streaming: stream, stream_options
# - Tools: tools, tool_choice, parallel_tool_calls
# - Structured Outputs: response_format
# - Diagnostics: logprobs, top_logprobs
# - Advanced token control: logit_bias
# - Vision: image_url content part
# 이번 PR에서 /playground 실제 구현은 하지 않는다.


class GatewayClients:
    def __init__(self, settings: AppSettings) -> None:
        self.main_llm = VLLMClient(settings.runtime("main_llm"))
        self.embedding = VLLMClient(settings.runtime("embedding"))
        self.risk_adapter = VLLMClient(
            RuntimeEndpoint(
                logical_id="risk-adapter",
                base_url=settings.risk_adapter_base_url,
                model="risk-adapter",
                timeout_seconds=settings.risk_adapter_timeout_seconds,
                # Allow risk forwarding requests and readiness probes to proceed
                # concurrently. The risk_adapter service enforces its own
                # admission control; the gateway semaphore here only needs to
                # prevent readiness probes from queuing behind user requests.
                max_concurrency=4,
            )
        )

    async def close(self) -> None:
        for client in (self.main_llm, self.embedding, self.risk_adapter):
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()


def create_gateway_app(settings: AppSettings | None = None, clients: GatewayClients | None = None) -> FastAPI:
    settings = settings or load_settings()
    clients = clients or GatewayClients(settings)
    metrics = Metrics("gateway")
    logger = service_logger("gateway")
    service = GatewayService(settings, clients, metrics)
    auth = require_bearer_auth(settings.security)
    api_dependencies = [Depends(auth)] if settings.security.api_key_required else []
    admin_dependencies = build_admin_dependencies(settings)

    app = create_service_app(
        title="AI Model Serving Gateway",
        version=settings.project_version,
        description=GATEWAY_DESCRIPTION_TEMPLATE,
        settings=settings,
        tags_metadata=GATEWAY_TAGS_METADATA,
        lifespan_resources=(clients,),
    )

    install_common_middleware(app, settings=settings, metrics=metrics, logger=logger)

    def validation_reason(exc: ServiceError) -> str:
        message = exc.message.lower()
        if "image_url" in message:
            return "image_input"
        if "max_tokens" in message:
            return "max_tokens"
        if "messages" in message:
            return "chat_messages"
        return "request"

    install_exception_handlers(app, metrics=metrics, logger=logger, validation_reason=validation_reason)
    register_scalar_docs(app, settings=settings, title="AI Model Serving Gateway")
    register_health(app, service="gateway", operation_id="getGatewayHealth")

    app.include_router(_build_ops_router(admin_dependencies, clients, metrics, settings))
    app.include_router(_build_inference_router(api_dependencies, service, settings))
    app.include_router(_build_risk_router(api_dependencies, service))

    _request_schemas, _response_schemas = schema_maps_from_specs(GATEWAY_ENDPOINTS)
    install_contract_openapi(
        app,
        request_schemas=_request_schemas,
        response_schemas=_response_schemas,
        request_examples={
            ("POST", "/v1/chat/completions"): GATEWAY_CHAT_REQUEST_EXAMPLES,
            ("POST", "/v1/embeddings"): GATEWAY_EMBEDDING_REQUEST_EXAMPLES,
            ("POST", "/v1/risk/detectors/prompt/assessments"): GATEWAY_RISK_PROMPT_REQUEST_EXAMPLES,
            ("POST", "/v1/risk/assessments"): GATEWAY_RISK_AGGREGATE_REQUEST_EXAMPLES,
        },
    )

    return app


app = create_gateway_app()
