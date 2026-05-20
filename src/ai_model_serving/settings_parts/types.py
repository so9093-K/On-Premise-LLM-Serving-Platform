from __future__ import annotations

from dataclasses import dataclass, field
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
class RiskDetectorSettings:
    key: str
    route: str
    service_key: str
    source_model: str
    family: str
    allowed_codes: frozenset[str]
    enabled: bool = True
    max_output_tokens: int = 1
    temperature: float = 0


@dataclass(frozen=True)
class AppSettings:
    app_env: str
    project_version: str
    security: SecuritySettings
    gateway_timeout_seconds: float
    risk_adapter_timeout_seconds: float
    risk_adapter_base_url: str
    runtime_endpoints: dict[str, RuntimeEndpoint] = field(default_factory=dict)
    risk_detectors: tuple[RiskDetectorSettings, ...] = ()
    aggregate_detector_order: tuple[str, ...] = ()
    main_llm: RuntimeEndpoint | None = None
    embedding: RuntimeEndpoint | None = None
    risk_prompt: RuntimeEndpoint | None = None
    max_request_body_bytes: int = 1_000_000
    risk_input_max_chars: int = 7_936
    public_models: tuple[dict[str, Any], ...] = ()
    documentation: DocumentationSettings = DocumentationSettings()
    readiness_probe_timeout_seconds: float = 2.0
    streaming_max_duration_seconds: float = 300.0
    streaming_max_chunks: int = 20_000
    streaming_max_bytes: int = 104_857_600

    def __post_init__(self) -> None:
        if not self.runtime_endpoints:
            endpoints = {
                key: endpoint
                for key, endpoint in {
                    "main_llm": self.main_llm,
                    "embedding": self.embedding,
                    "risk_prompt": self.risk_prompt,
                }.items()
                if endpoint is not None
            }
            object.__setattr__(self, "runtime_endpoints", endpoints)
        if not self.risk_detectors:
            detectors: list[RiskDetectorSettings] = []
            if self.risk_prompt is not None:
                detectors.append(
                    RiskDetectorSettings(
                        key="prompt",
                        route="/v1/risk/detectors/prompt/assessments",
                        service_key="risk_prompt",
                        source_model="risk-prompt",
                        family="prompt_attack",
                        allowed_codes=frozenset({"A1", "A2"}),
                    )
                )
            object.__setattr__(self, "risk_detectors", tuple(detectors))
        if not self.aggregate_detector_order:
            object.__setattr__(
                self,
                "aggregate_detector_order",
                tuple(detector.key for detector in self.risk_detectors if detector.enabled),
            )

    def runtime(self, key: str) -> RuntimeEndpoint:
        try:
            return self.runtime_endpoints[key]
        except KeyError as exc:
            raise KeyError(f"runtime endpoint is not configured or enabled: {key}") from exc

    def optional_runtime(self, key: str) -> RuntimeEndpoint | None:
        return self.runtime_endpoints.get(key)

    def enabled_risk_detectors(self) -> tuple[RiskDetectorSettings, ...]:
        return tuple(detector for detector in self.risk_detectors if detector.enabled)
