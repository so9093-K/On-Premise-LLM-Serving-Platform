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
    max_audio_inputs: int = 0
    allowed_audio_formats: tuple[str, ...] = ()
    max_audio_bytes: int = 0
    request_parameter_policy: dict[str, Any] | None = None
    runtime_features: dict[str, Any] | None = None


@dataclass(frozen=True)
class EmbeddingProfile:
    model: str
    service_key: str
    upstream_model_id: str
    dimensions: tuple[int, ...]
    default_dimensions: int
    purpose: str = "general"
    retrieval_enabled: bool = False
    retrieval_default: bool = False
    score_modes: tuple[str, ...] = ()
    prompt_policy: dict[str, Any] = field(default_factory=dict)
    request_parameter_policy: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskDetectorSettings:
    key: str
    route: str
    source_model: str
    family: str
    allowed_codes: frozenset[str]
    service_key: str = ""
    detector_type: str = "vllm"
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
    embedding_ko: RuntimeEndpoint | None = None
    risk_prompt: RuntimeEndpoint | None = None
    embedding_profiles: dict[str, EmbeddingProfile] = field(default_factory=dict)
    embedding_model_routes: dict[str, str] = field(default_factory=dict)
    default_embedding_model: str = "local-embed"
    default_retrieval_model: str = "local-embed-ko"
    max_request_body_bytes: int = 1_000_000
    max_retrieval_documents: int = 32
    risk_input_max_chars: int = 7_936
    public_models: tuple[dict[str, Any], ...] = ()
    documentation: DocumentationSettings = DocumentationSettings()
    readiness_probe_timeout_seconds: float = 2.0
    streaming_max_duration_seconds: float = 300.0
    streaming_max_chunks: int = 20_000
    streaming_max_bytes: int = 104_857_600
    admin_sidecar_url: str = ""
    deploy_release_id: str = ""

    def __post_init__(self) -> None:
        if not self.runtime_endpoints:
            endpoints = {
                key: endpoint
                for key, endpoint in {
                    "main_llm": self.main_llm,
                    "embedding": self.embedding,
                    "embedding_ko": self.embedding_ko,
                    "risk_prompt": self.risk_prompt,
                }.items()
                if endpoint is not None
            }
            object.__setattr__(self, "runtime_endpoints", endpoints)
        if not self.embedding_profiles and self.embedding is not None:
            profile = EmbeddingProfile(
                model=self.embedding.model,
                service_key="embedding",
                upstream_model_id="",
                dimensions=tuple(int(item) for item in (self.embedding.request_parameter_policy or {}).get("dimensions", (768, 512, 256, 128))),
                default_dimensions=768,
                retrieval_enabled=True,
                retrieval_default=False,
                score_modes=("dense_cosine",),
                request_parameter_policy=self.embedding.request_parameter_policy or {},
            )
            object.__setattr__(self, "embedding_profiles", {profile.model: profile})
            # Legacy AppSettings construction path: older tests/extensions may
            # supply only one embedding endpoint. Synthesize a matching profile
            # and align defaults so validation still has one source of truth.
            if self.default_embedding_model not in {profile.model}:
                object.__setattr__(self, "default_embedding_model", profile.model)
            if self.default_retrieval_model not in {profile.model}:
                object.__setattr__(self, "default_retrieval_model", profile.model)
        if not self.embedding_model_routes and self.embedding_profiles:
            object.__setattr__(self, "embedding_model_routes", {model: profile.service_key for model, profile in self.embedding_profiles.items()})
        self._validate_embedding_configuration()
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
                        detector_type="vllm",
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

    def _validate_embedding_configuration(self) -> None:
        if not self.embedding_profiles and not self.embedding_model_routes:
            return
        profile_keys = set(self.embedding_profiles)
        route_keys = set(self.embedding_model_routes)
        if profile_keys != route_keys:
            missing_routes = sorted(profile_keys - route_keys)
            missing_profiles = sorted(route_keys - profile_keys)
            details: list[str] = []
            if missing_routes:
                details.append(f"missing routes for embedding profiles: {', '.join(missing_routes)}")
            if missing_profiles:
                details.append(f"routes without embedding profiles: {', '.join(missing_profiles)}")
            raise ValueError("; ".join(details))
        models_by_service_key: dict[str, set[str]] = {}
        for profile in self.embedding_profiles.values():
            models_by_service_key.setdefault(profile.service_key, set()).add(profile.model)
        for model_id, profile in self.embedding_profiles.items():
            if profile.model != model_id:
                raise ValueError(f"embedding profile key {model_id!r} must match profile.model {profile.model!r}")
            route_service_key = self.embedding_model_routes.get(model_id)
            if route_service_key != profile.service_key:
                raise ValueError(
                    f"embedding route for {model_id!r} points to {route_service_key!r}, "
                    f"but profile.service_key is {profile.service_key!r}"
                )
            if profile.service_key not in self.runtime_endpoints:
                raise ValueError(f"embedding profile {model_id!r} references unknown runtime service {profile.service_key!r}")
            runtime = self.runtime_endpoints[profile.service_key]
            service_models = models_by_service_key[profile.service_key]
            if runtime.model not in service_models:
                raise ValueError(
                    f"embedding runtime {profile.service_key!r} serves model {runtime.model!r}, "
                    f"but embedding profiles for that service expect one of {', '.join(sorted(service_models))}"
                )
        for attr_name, model_id in (
            ("default_embedding_model", self.default_embedding_model),
            ("default_retrieval_model", self.default_retrieval_model),
        ):
            if model_id not in self.embedding_profiles:
                raise ValueError(f"{attr_name} {model_id!r} is not configured in embedding_profiles")
