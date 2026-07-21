from __future__ import annotations

import os
from pathlib import Path
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
from ..security import require_bearer_auth
from ..settings import AppSettings, RuntimeEndpoint, SecuritySettings, load_settings
from ..services.gateway_service import GatewayService
from ..upstream import VLLMClient
from ..api_descriptions import GATEWAY_DESCRIPTION_TEMPLATE, GATEWAY_TAGS_METADATA
from ..api_examples import (
    GATEWAY_CHAT_REQUEST_EXAMPLES,
    GATEWAY_EMBEDDING_REQUEST_EXAMPLES,
    GATEWAY_RETRIEVAL_RERANK_REQUEST_EXAMPLES,
    GATEWAY_RETRIEVAL_SCORE_REQUEST_EXAMPLES,
    GATEWAY_RISK_AGGREGATE_REQUEST_EXAMPLES,
    GATEWAY_RISK_PROMPT_REQUEST_EXAMPLES,
    PII_EXAMPLES,
    SECRET_EXAMPLES,
)
from ..api.endpoint_spec import GATEWAY_ENDPOINTS, schema_maps_from_specs
from ..api.routers.gateway_ops import build_router as _build_ops_router
from ..api.routers.gateway_inference import build_router as _build_inference_router
from ..api.routers.gateway_risk import build_router as _build_risk_router
from ..api.routers.gateway_retrieval import build_router as _build_retrieval_router
from ..api.routers.gateway_runtime_control import build_router as _build_runtime_control_router
from ..services.runtime_state import RuntimeStateStore
from ..services.sidecar_client import SidecarClient
from ..services.main_model_inflight import MainModelInFlight
from ..runtime_topology import load_runtime_topology

# TODO(playground): /playground 구현 시 /v1/models[].request_parameters를 읽어
# model-aware form을 동적으로 구성한다. 아래 grouping을 참고한다:
# - 기본 생성: max_tokens, temperature, top_p
# - 고급 샘플링: top_k, min_p, presence_penalty, frequency_penalty, repetition_penalty, seed, n
# - 스트리밍: stream, stream_options
# - 도구: tools, tool_choice, parallel_tool_calls
# - Structured Outputs: response_format
# - 진단: logprobs, top_logprobs
# - 고급 토큰 제어: logit_bias
# - 비전: image_url content part
# 이번 PR에서 /playground 실제 구현은 하지 않는다.


class GatewayClients:
    def __init__(self, settings: AppSettings) -> None:
        state_path = os.environ.get("GATEWAY_RUNTIME_STATE_PATH")
        topology = load_runtime_topology(
            Path(os.environ.get("APP_CONFIG_ROOT", Path(__file__).resolve().parents[3]))
        )
        self.runtime_state = RuntimeStateStore(
            Path(state_path) if state_path else None,
            controllable_keys=topology.controllable_keys,
        )
        self.main_model_inflight = MainModelInFlight()
        self.sidecar: SidecarClient | None = (
            SidecarClient(
                settings.admin_sidecar_url,
                settings.security.internal_service_token,
            )
            if settings.admin_sidecar_url
            else None
        )
        self.main_llm = VLLMClient(settings.runtime("main_llm"))
        self.runtime_clients_by_service_key: dict[str, VLLMClient] = {}
        self.embedding_clients: dict[str, VLLMClient] = {}
        for model_id, service_key in settings.embedding_model_routes.items():
            client = self.runtime_clients_by_service_key.get(service_key)
            if client is None:
                client = VLLMClient(settings.runtime(service_key))
                self.runtime_clients_by_service_key[service_key] = client
            self.embedding_clients[model_id] = client
        self.risk_adapter = VLLMClient(
            RuntimeEndpoint(
                logical_id="risk-adapter",
                base_url=settings.risk_adapter_base_url,
                model="risk-adapter",
                timeout_seconds=settings.risk_adapter_timeout_seconds,
                # risk forwarding 요청과 readiness probe가 동시에 진행되도록
                # 허용한다. risk_adapter 서비스가 자체적으로 admission control을
                # 수행하므로, 여기 gateway semaphore는 readiness probe가 사용자
                # 요청 뒤에서 대기열에 밀리지 않도록 막는 역할만 하면 된다.
                max_concurrency=4,
            )
        )
        self.runtimes: dict[str, Any] = {
            "main_llm": self.main_llm,
            "risk_adapter": self.risk_adapter,
        }
        self.runtimes.update(self.runtime_clients_by_service_key)

    async def close(self) -> None:
        seen: set[int] = set()
        for client in (self.main_llm, *self.runtime_clients_by_service_key.values(), self.risk_adapter):
            if client is None:
                continue
            if id(client) in seen:
                continue
            seen.add(id(client))
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()


def create_gateway_app(settings: AppSettings | None = None, clients: GatewayClients | None = None) -> FastAPI:
    settings = settings or load_settings()
    clients = clients or GatewayClients(settings)
    if not hasattr(clients, "main_model_inflight"):
        clients.main_model_inflight = MainModelInFlight()
    metrics = Metrics("gateway")
    logger = service_logger("gateway")
    service = GatewayService(settings, clients, metrics)
    auth = require_bearer_auth(settings.security)
    api_dependencies = [Depends(auth)] if settings.security.api_key_required else []
    admin_dependencies = build_admin_dependencies(settings)

    _release = settings.deploy_release_id
    _version = (
        f"{settings.project_version} ({_release[:8]})" if _release else settings.project_version
    )
    app = create_service_app(
        title="AI Model Serving Gateway",
        version=_version,
        description=GATEWAY_DESCRIPTION_TEMPLATE,
        settings=settings,
        tags_metadata=GATEWAY_TAGS_METADATA,
        lifespan_resources=(clients,),
    )

    install_common_middleware(app, settings=settings, metrics=metrics, logger=logger)

    def validation_reason(exc: ServiceError) -> str:
        message = exc.message.lower()
        if "image_url" in message:
            if "byte" in message:
                return "image_bytes"
            if "pixel" in message:
                return "image_pixels"
            if "mime" in message:
                return "image_mime"
            return "image_input"
        if "input_audio" in message:
            if "byte" in message:
                return "audio_bytes"
            if "format" in message:
                return "audio_format"
            return "audio_input"
        if "video_url" in message:
            if "play" in message:
                return "video_duration"
            if "frame" in message:
                return "video_frames"
            if "byte" in message:
                return "video_bytes"
            if "mime" in message:
                return "video_mime"
            return "video_input"
        if "max_tokens" in message:
            return "max_tokens"
        if "messages" in message:
            return "chat_messages"
        return "request"

    install_exception_handlers(app, metrics=metrics, logger=logger, validation_reason=validation_reason)
    register_scalar_docs(app, settings=settings, title="AI Model Serving Gateway")
    register_health(app, service="gateway", operation_id="getGatewayHealth")

    app.include_router(_build_ops_router(admin_dependencies, clients, metrics, settings))
    app.include_router(
        _build_inference_router(
            api_dependencies,
            service,
            settings,
            clients.runtime_state,
            clients.sidecar,
            clients.main_model_inflight,
        )
    )
    app.include_router(_build_risk_router(api_dependencies, service, clients.runtime_state))
    app.include_router(_build_retrieval_router(api_dependencies, admin_dependencies, service, settings, clients.runtime_state))
    app.include_router(_build_runtime_control_router(admin_dependencies, clients.runtime_state, clients.sidecar, settings))

    @app.get(
        "/internal/main-model/drain-status",
        include_in_schema=False,
        operation_id="getMainModelDrainStatus",
    )
    async def main_model_drain_status(
        _: None = Depends(require_bearer_auth(
            SecuritySettings(
                api_key_required=settings.security.internal_service_auth_required,
                api_keys=(
                    frozenset({settings.security.internal_service_token})
                    if settings.security.internal_service_token
                    else frozenset()
                ),
                internal_service_token=settings.security.internal_service_token,
            )
        )),
    ) -> dict[str, int]:
        return {"in_flight": await clients.main_model_inflight.count()}

    _request_schemas, _response_schemas = schema_maps_from_specs(GATEWAY_ENDPOINTS)
    install_contract_openapi(
        app,
        request_schemas=_request_schemas,
        response_schemas=_response_schemas,
        request_examples={
            ("POST", "/v1/chat/completions"): GATEWAY_CHAT_REQUEST_EXAMPLES,
            ("POST", "/v1/embeddings"): GATEWAY_EMBEDDING_REQUEST_EXAMPLES,
            ("POST", "/v1/risk/detectors/prompt/assessments"): GATEWAY_RISK_PROMPT_REQUEST_EXAMPLES,
            ("POST", "/v1/risk/detectors/pii/assessments"): PII_EXAMPLES,
            ("POST", "/v1/risk/detectors/secret/assessments"): SECRET_EXAMPLES,
            ("POST", "/v1/risk/assessments"): GATEWAY_RISK_AGGREGATE_REQUEST_EXAMPLES,
            ("POST", "/v1/retrieval/rerank"): GATEWAY_RETRIEVAL_RERANK_REQUEST_EXAMPLES,
            ("POST", "/v1/retrieval/score"): GATEWAY_RETRIEVAL_SCORE_REQUEST_EXAMPLES,
            ("PATCH", "/admin/runtimes/{service_key}"): {
                "to_stopped": {"summary": "중지 (VRAM 회수)", "value": {"desired_state": "stopped"}},
                "to_active": {"summary": "시작 (서비스 복구)", "value": {"desired_state": "active"}},
            },
        },
    )

    return app
