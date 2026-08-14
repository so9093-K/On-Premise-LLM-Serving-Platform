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
class CorsSettings:
    # 브라우저 기반 webui가 다른 origin에서 이 API를 호출할 때 필요. 기본값은 전체
    # 허용("*") — 이 프로젝트의 기본 auth profile(local_open)이 이미 "네트워크 경계가
    # 접근 제어를 소유한다"는 전제로 API 키 인증까지 기본으로 끄고 있고, vLLM 자체도
    # 기본이 allow_origins=["*"]라 그것과 맞춘다. Bearer 토큰 인증(쿠키 아님)이라
    # allow_credentials는 안 쓴다. 더 엄격한 프로필에서는 CORS_ALLOWED_ORIGINS를
    # 명시적으로 좁히거나 빈 값으로 두면 미들웨어 자체가 안 붙는다.
    allowed_origins: tuple[str, ...] = ("*",)


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
    max_video_inputs: int = 0
    allowed_video_url_schemes: tuple[str, ...] = ()
    allowed_video_mime_types: tuple[str, ...] = ()
    max_video_bytes: int = 0
    max_video_frames: int = 0
    max_video_frame_pixels: int = 0
    max_video_duration_seconds: float = 0
    request_parameter_policy: dict[str, Any] | None = None
    runtime_features: dict[str, Any] | None = None


@dataclass(frozen=True)
class EmbeddingProfile:
    model: str
    service_key: str
    default_dimensions: int
    retrieval_enabled: bool = False
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
    # Sidecar 없이 Gateway를 단독 실행할 때 사용할 default profile의 정책이다.
    # 실제 full-stack 요청은 active_profile snapshot의 정책으로 항상 덮어쓴다.
    default_main_model_gateway_policy: dict[str, Any] = field(default_factory=dict)
    embedding_profiles: dict[str, EmbeddingProfile] = field(default_factory=dict)
    default_embedding_model: str = ""
    default_retrieval_model: str = ""
    max_request_body_bytes: int = 100_000_000
    max_retrieval_documents: int = 32
    risk_input_max_chars: int = 7_936
    public_models: tuple[dict[str, Any], ...] = ()
    documentation: DocumentationSettings = DocumentationSettings()
    cors: CorsSettings = CorsSettings()
    readiness_probe_timeout_seconds: float = 2.0
    streaming_max_duration_seconds: float = 300.0
    streaming_max_chunks: int = 20_000
    streaming_max_bytes: int = 104_857_600
    admin_sidecar_url: str = ""
    deploy_release_id: str = ""
    log_request_response_body: bool = False

    def __post_init__(self) -> None:
        self._validate_embedding_configuration()
        if not self.risk_detectors:
            detectors: list[RiskDetectorSettings] = []
            if "risk_prompt" in self.runtime_endpoints:
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

    def enabled_risk_detectors(self) -> tuple[RiskDetectorSettings, ...]:
        return tuple(detector for detector in self.risk_detectors if detector.enabled)

    def _validate_embedding_configuration(self) -> None:
        if not self.embedding_profiles:
            raise ValueError("embedding_profiles must be explicitly configured")
        models_by_service_key: dict[str, set[str]] = {}
        for profile in self.embedding_profiles.values():
            models_by_service_key.setdefault(profile.service_key, set()).add(profile.model)
        for model_id, profile in self.embedding_profiles.items():
            if profile.model != model_id:
                raise ValueError(f"embedding profile key {model_id!r} must match profile.model {profile.model!r}")
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
