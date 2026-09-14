from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI

from ..app_kernel import (
    admin_dependencies as build_admin_dependencies,
    create_service_app,
    install_common_middleware,
    install_cors_middleware,
    install_exception_handlers,
    register_health,
    register_documentation_ui,
)
from ..configuration_mutation import ConfigurationHistoryStore, ConfigurationMutationEngine
from ..errors import ServiceError
from ..service_logging import service_logger
from ..metrics import Metrics
from ..api_descriptions import (
    chat_operation_detail,
    embeddings_operation_detail,
    models_operation_detail,
)
from ..configuration_plane import configuration_schema_items
from ..operator_configuration import (
    ConfigurationValueResolver,
    OperatorConfigurationState,
    OperatorConfigurationStore,
    operator_configuration_path,
    operator_metadata_by_key,
    repository_operator_defaults,
    runtime_snapshot_from_resolver,
)
from ..openapi_contracts import install_contract_openapi, narrow_chat_request_schema
from ..runtime_configuration import RuntimeConfigurationProvider
from ..security import require_bearer_auth
from ..settings import AppSettings, RuntimeEndpoint, SecuritySettings, load_settings
from ..services.gateway_service import GatewayService
from ..upstream import RuntimeClient
from ..api_descriptions import gateway_description, gateway_tags_metadata
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
from ..api.endpoint_spec import GATEWAY_ENDPOINTS, error_codes_from_specs, schema_maps_from_specs

_GW_SPECS = {(spec.method, spec.path): spec for spec in GATEWAY_ENDPOINTS}
from ..api.routers.gateway_ops import build_router as _build_ops_router
from ..api.routers.gateway_configuration import build_router as _build_configuration_router
from ..api.routers.gateway_inference import build_router as _build_inference_router
from ..api.routers.gateway_risk import build_router as _build_risk_router
from ..api.routers.gateway_retrieval import build_router as _build_retrieval_router
from ..api.routers.gateway_runtime_control import build_router as _build_runtime_control_router
from ..services.runtime_state import RuntimeStateStore
from ..services.sidecar_client import SidecarClient
from ..services.main_model_inflight import MainModelInFlight


class GatewayClients:
    def __init__(self, settings: AppSettings) -> None:
        state_path = os.environ.get("GATEWAY_RUNTIME_STATE_PATH")

        def _runtime_directive(name: str) -> list[str]:
            return [key.strip() for key in os.environ.get(name, "").split(",") if key.strip()]

        self.runtime_state = RuntimeStateStore(
            Path(state_path) if state_path else None,
            controllable_keys=settings.controllable_runtime_keys,
            deferred_keys=_runtime_directive("DEPLOY_DEFERRED_RUNTIMES"),
            activated_keys=_runtime_directive("DEPLOY_ACTIVE_RUNTIMES"),
            release_id=settings.deploy_release_id,
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
        self.main_llm = RuntimeClient(settings.runtime("main_llm"))
        self.runtime_clients_by_service_key: dict[str, RuntimeClient] = {}
        self.embedding_clients: dict[str, RuntimeClient] = {}
        for model_id, profile in settings.embedding_profiles.items():
            service_key = profile.service_key
            client = self.runtime_clients_by_service_key.get(service_key)
            if client is None:
                client = RuntimeClient(settings.runtime(service_key))
                self.runtime_clients_by_service_key[service_key] = client
            self.embedding_clients[model_id] = client
        self.risk_adapter = (
            RuntimeClient(
                RuntimeEndpoint(
                    logical_id="risk-adapter",
                    base_url=settings.risk_adapter_base_url,
                    model="risk-adapter",
                    timeout_seconds=settings.risk_adapter_timeout_seconds,
                    max_concurrency=4,
                )
            )
            if settings.feature_enabled("risk")
            else None
        )
        self.runtimes: dict[str, Any] = {"main_llm": self.main_llm}
        if self.risk_adapter is not None:
            self.runtimes["risk_adapter"] = self.risk_adapter
        self.runtimes.update(self.runtime_clients_by_service_key)

    async def close(self) -> None:
        seen: set[int] = set()
        for client in (
            self.main_llm,
            *self.runtime_clients_by_service_key.values(),
            self.risk_adapter,
            self.sidecar,
        ):
            if client is None or id(client) in seen:
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

    schema_items = configuration_schema_items()
    runtime_configuration = RuntimeConfigurationProvider.from_settings(settings)
    metadata = operator_metadata_by_key(schema_items)
    state_path = operator_configuration_path()
    operator_store: OperatorConfigurationStore | None = None
    history_store: ConfigurationHistoryStore | None = None
    operator_state = OperatorConfigurationState(revision=0, overrides={})
    if state_path is not None:
        operator_store = OperatorConfigurationStore(state_path, metadata)
        history_store = ConfigurationHistoryStore(state_path.parent / "history")
        # Corrupt or invalid persistent operator state is a startup error. Silently
        # ignoring it would make the effective API disagree with operator intent.
        operator_state = operator_store.read()

    configuration_resolver = ConfigurationValueResolver(
        repository_defaults=repository_operator_defaults(schema_items),
        operator_state=operator_state,
    )
    if state_path is not None:
        runtime_configuration.install(
            runtime_snapshot_from_resolver(configuration_resolver, schema_items)
        )

    configuration_mutation = ConfigurationMutationEngine(
        schema_items=schema_items,
        store=operator_store,
        resolver=configuration_resolver,
        runtime_configuration=runtime_configuration,
        history=history_store,
    )
    # Journal은 desired state보다 먼저 기록되므로 process crash가 있어도 pending
    # operation이 남는다. Persisted override hydration이 끝난 뒤 그 기록을 실제
    # store/resolver/runtime revision과 대조해 terminal recovery 상태로 닫는다.
    configuration_mutation.recover_interrupted_operations()

    runtime_settings = runtime_configuration.settings_view(settings)
    service = GatewayService(runtime_settings, clients, metrics)
    auth = require_bearer_auth(settings.security)
    api_dependencies = [Depends(auth)] if settings.security.api_key_required else []
    admin_dependencies = build_admin_dependencies(settings)

    _release = settings.deploy_release_id
    _version = f"{settings.project_version} ({_release[:8]})" if _release else settings.project_version
    app = create_service_app(
        title="AI Model Serving Gateway",
        version=_version,
        description=gateway_description(settings),
        settings=settings,
        tags_metadata=gateway_tags_metadata(settings),
        lifespan_resources=(clients,),
    )
    app.state.runtime_configuration = runtime_configuration
    app.state.operator_configuration_store = operator_store
    app.state.configuration_history_store = history_store
    app.state.configuration_resolver = configuration_resolver
    app.state.configuration_mutation = configuration_mutation

    install_common_middleware(app, settings=settings, metrics=metrics, logger=logger)
    install_cors_middleware(app, settings=settings)

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
    register_documentation_ui(app, settings=settings, title="AI Model Serving Gateway")
    register_health(app, service="gateway", spec=_GW_SPECS[("GET", "/health")])

    app.include_router(_build_ops_router(admin_dependencies, clients, metrics, settings))
    app.include_router(
        _build_configuration_router(
            admin_dependencies,
            runtime_settings,
            configuration_resolver,
            configuration_mutation,
        )
    )
    app.include_router(
        _build_inference_router(
            api_dependencies,
            service,
            settings,
            clients.runtime_state,
            clients.sidecar,
            clients.main_model_inflight,
            include_embeddings=settings.feature_enabled("embeddings"),
        )
    )
    if settings.feature_enabled("risk"):
        app.include_router(_build_risk_router(api_dependencies, service, settings, clients.runtime_state))
    if settings.feature_enabled("retrieval"):
        app.include_router(
            _build_retrieval_router(
                api_dependencies, admin_dependencies, service, settings, clients.runtime_state
            )
        )
    if settings.feature_enabled("runtime_control"):
        app.include_router(
            _build_runtime_control_router(
                admin_dependencies, clients.runtime_state, clients.sidecar, settings
            )
        )

    if settings.feature_enabled("model_switching"):
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
        error_codes=error_codes_from_specs(GATEWAY_ENDPOINTS),
        operation_details={
            ("POST", "/v1/chat/completions"): chat_operation_detail(settings),
            ("GET", "/v1/models"): models_operation_detail(settings),
            ("POST", "/v1/embeddings"): embeddings_operation_detail(settings),
        },
        schema_narrowers={
            ("POST", "/v1/chat/completions"): lambda schema: narrow_chat_request_schema(
                schema, settings.main_model_profile_policies
            ),
        },
        request_examples={
            ("POST", "/v1/chat/completions"): GATEWAY_CHAT_REQUEST_EXAMPLES,
            ("POST", "/v1/embeddings"): GATEWAY_EMBEDDING_REQUEST_EXAMPLES,
            ("POST", "/v1/risk/detectors/prompt/assessments"): GATEWAY_RISK_PROMPT_REQUEST_EXAMPLES,
            ("POST", "/v1/risk/detectors/pii/assessments"): PII_EXAMPLES,
            ("POST", "/v1/risk/detectors/secret/assessments"): SECRET_EXAMPLES,
            ("POST", "/v1/risk/assessments"): GATEWAY_RISK_AGGREGATE_REQUEST_EXAMPLES,
            ("POST", "/v1/retrieval/rerank"): GATEWAY_RETRIEVAL_RERANK_REQUEST_EXAMPLES,
            ("POST", "/v1/retrieval/score"): GATEWAY_RETRIEVAL_SCORE_REQUEST_EXAMPLES,
        },
    )

    return app