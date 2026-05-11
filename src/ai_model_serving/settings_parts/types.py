from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SecuritySettings:
    api_key_required: bool
    api_keys: frozenset[str]
    internal_service_token: str
    internal_service_auth_required: bool = True
    auth_mode: str = "custom"
    admin_api_key_required: bool = False
    admin_api_keys: frozenset[str] = frozenset()
    admin_endpoints_internal_only: bool = False


@dataclass(frozen=True)
class DocumentationSettings:
    enabled: bool = True
    docs_url: str = "/docs"
    redoc_url: str = "/redoc"
    openapi_url: str = "/openapi.json"


@dataclass(frozen=True)
class RuntimeEndpoint:
    logical_id: str
    base_url: str
    model: str
    timeout_seconds: float
    max_concurrency: int = 1
    queue_timeout_seconds: float = 2.0
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_reset_seconds: float = 15.0
    http_max_connections: int = 100
    http_max_keepalive_connections: int = 20
    max_output_tokens: int | None = None
    max_model_len: int | None = None
    allowed_input_modalities: tuple[str, ...] = ("text",)
    max_image_inputs: int = 0
    allowed_image_url_schemes: tuple[str, ...] = ()
    max_image_bytes: int = 0
    max_image_pixels: int = 0
    allowed_image_mime_types: tuple[str, ...] = ()
    request_parameter_policy: dict[str, Any] | None = None
    runtime_features: dict[str, Any] | None = None


@dataclass(frozen=True)
class AppSettings:
    app_env: str
    project_version: str
    security: SecuritySettings
    gateway_timeout_seconds: float
    risk_adapter_timeout_seconds: float
    main_llm: RuntimeEndpoint
    embedding: RuntimeEndpoint
    risk_prompt: RuntimeEndpoint
    risk_siren: RuntimeEndpoint
    risk_adapter_base_url: str
    max_request_body_bytes: int = 1_000_000
    risk_input_max_chars: int = 7_936
    public_models: tuple[dict[str, Any], ...] = ()
    documentation: DocumentationSettings = DocumentationSettings()
