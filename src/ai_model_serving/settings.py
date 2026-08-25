from __future__ import annotations

from pathlib import Path
import os
from typing import Any

from .domain import ModelRegistry
from .main_model.control import load_main_model_catalog
from .risk_input import detector_prompt_char_budget
from .configuration import load_yaml_mapping
from .project_paths import resolve_project_root as _resolve_project_root
from .settings_parts.env import (
    as_bool as _as_bool,
    as_float as _as_float,
    as_int as _as_int,
    env as _env,
    load_local_dotenv_when_allowed,
)
from .settings_parts.runtime_endpoints import build_runtime_endpoint, validate_timeout_budget
from .settings_parts.security import build_security_settings
from .settings_parts.types import AppSettings, CorsSettings, DocumentationSettings, EmbeddingProfile, RiskDetectorSettings, RuntimeEndpoint, SecuritySettings

ROOT = _resolve_project_root()


def _public_models_from_registry(
    model_catalog: dict[str, Any],
    model_serving: dict[str, Any],
    default_main_model_gateway_policy: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    # ModelRegistry는 공개 목록의 공통 projection을 담당한다. main_llm의 API
    # parameter 표면만은 default Profile에서 주입한다. 실제 요청 시에는 Gateway가
    # active Profile snapshot으로 다시 덮어쓴다.
    projected_serving = dict(model_serving)
    projected_models = dict(model_serving.get("models", {}))
    main_llm = dict(projected_models.get("main_llm", {}))
    main_llm["max_output_tokens"] = default_main_model_gateway_policy["max_output_tokens"]
    main_llm["request_parameter_policy"] = default_main_model_gateway_policy["request_parameter_policy"]
    projected_models["main_llm"] = main_llm
    projected_serving["models"] = projected_models
    registry = ModelRegistry(model_catalog, projected_serving)
    issues = registry.alignment_issues()
    if issues:
        details = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        raise RuntimeError(f"Model registry and serving config are not aligned: {details}")
    return registry.public_model_response_items()


def _documentation_settings(documentation_cfg: dict[str, Any]) -> DocumentationSettings:
    docs_enabled = _as_bool(
        _env("FASTAPI_DOCS_ENABLED", str(documentation_cfg.get("enabled", True))),
        True,
    )
    return DocumentationSettings(
        enabled=docs_enabled,
        docs_url=_env("FASTAPI_DOCS_URL", str(documentation_cfg.get("docs_url", "/docs"))),
        redoc_url=_env("FASTAPI_REDOC_URL", str(documentation_cfg.get("redoc_url", "/redoc"))),
        openapi_url=_env("OPENAPI_URL", str(documentation_cfg.get("openapi_url", "/openapi.json"))),
    )


def _cors_settings() -> CorsSettings:
    # 이 프로젝트의 기본 auth profile(local_open)이 API 키 인증까지 기본으로 끄고
    # "네트워크 경계가 접근 제어를 소유한다"는 철학이라(configs/auth_profiles.yaml),
    # CORS만 기본으로 닫아두는 건 오히려 이 프로젝트 기조와 어긋난다. vLLM 자체도
    # 기본이 allow_origins=["*"]다 — 그것과 동일하게 맞춘다. 더 엄격한 프로필로
    # 운영하려면 CORS_ALLOWED_ORIGINS를 명시적으로 좁히거나 빈 값으로 두면 된다.
    origins = tuple(origin.strip() for origin in _env("CORS_ALLOWED_ORIGINS", "*").split(",") if origin.strip())
    return CorsSettings(allowed_origins=origins)


def _env_name(model_key: str, suffix: str) -> str:
    return f"{model_key.upper()}_{suffix}"


def _build_runtime_endpoints(
    *,
    models: dict[str, Any],
    timeout: float,
    operational_limits: dict[str, Any],
) -> dict[str, RuntimeEndpoint]:
    endpoints: dict[str, RuntimeEndpoint] = {}
    for model_key, cfg in models.items():
        if cfg.get("enabled", True) is not True:
            continue
        endpoints[str(model_key)] = build_runtime_endpoint(
            model_key=str(model_key),
            env_url=_env_name(str(model_key), "BASE_URL"),
            env_model=_env_name(str(model_key), "MODEL"),
            timeout=timeout,
            models=models,
            operational_limits=operational_limits,
        )
    return endpoints


def _risk_detectors_from_config(risk_adapter_cfg: dict[str, Any]) -> tuple[RiskDetectorSettings, ...]:
    detectors_cfg = risk_adapter_cfg.get("detectors")
    if not isinstance(detectors_cfg, dict) or not detectors_cfg:
        raise RuntimeError("risk_adapter.detectors must be a non-empty mapping in configs/model_serving.yaml")
    detectors: list[RiskDetectorSettings] = []
    for key, cfg in detectors_cfg.items():
        fixed = cfg.get("fixed_parameters", {}) if isinstance(cfg.get("fixed_parameters", {}), dict) else {}
        detector_type = str(cfg.get("type", "vllm"))
        # local detector는 service_key가 필요하지 않다; 기본값은 빈 문자열로 둔다.
        service_key = str(cfg.get("service_key", "")) if detector_type == "local" else str(cfg["service_key"])
        detectors.append(
            RiskDetectorSettings(
                key=str(key),
                route=str(cfg.get("route", f"/v1/risk/detectors/{key}/assessments")),
                service_key=service_key,
                source_model=str(cfg.get("source_model", key)),
                family=str(cfg["family"]),
                allowed_codes=frozenset(str(item) for item in cfg.get("allowed_codes", [])),
                detector_type=detector_type,
                enabled=cfg.get("enabled", True) is True,
                max_output_tokens=int(fixed.get("max_tokens", cfg.get("max_output_tokens", 1))),
                temperature=float(fixed.get("temperature", cfg.get("temperature", 0))),
            )
        )
    return tuple(detectors)


def _embedding_profiles_from_config(model_serving: dict[str, Any]) -> dict[str, EmbeddingProfile]:
    profiles_cfg = model_serving.get("embedding_profiles")
    if not isinstance(profiles_cfg, dict) or not profiles_cfg:
        raise RuntimeError("embedding_profiles must be a non-empty mapping in configs/model_serving.yaml")
    models = model_serving.get("models", {})
    profiles: dict[str, EmbeddingProfile] = {}
    for model_id, cfg in profiles_cfg.items():
        retrieval = cfg.get("retrieval", {}) if isinstance(cfg.get("retrieval", {}), dict) else {}
        service_key = str(cfg["service_key"])
        runtime_cfg = models.get(service_key, {}) if isinstance(models.get(service_key, {}), dict) else {}
        request_policy = cfg.get("request_parameter_policy")
        if not isinstance(request_policy, dict):
            request_policy = runtime_cfg.get("request_parameter_policy", {})
        prompt_policy = dict(cfg.get("prompt_policy", {})) if isinstance(cfg.get("prompt_policy", {}), dict) else {}
        for role, policy in prompt_policy.items():
            if not isinstance(policy, dict):
                raise RuntimeError(f"embedding_profiles.{model_id}.prompt_policy.{role} must be an object")
            mode = str(policy.get("mode", "none"))
            if mode not in {"none", "prefix"}:
                raise RuntimeError(
                    f"embedding_profiles.{model_id}.prompt_policy.{role}.mode must be 'none' or 'prefix'"
                )
            if mode == "prefix" and not isinstance(policy.get("prefix", ""), str):
                raise RuntimeError(
                    f"embedding_profiles.{model_id}.prompt_policy.{role}.prefix must be a string"
                )
        profiles[str(model_id)] = EmbeddingProfile(
            model=str(cfg.get("served_model_name", model_id)),
            service_key=service_key,
            default_dimensions=int(cfg["default_dimensions"]),
            retrieval_enabled=retrieval.get("enabled", False) is True,
            prompt_policy=prompt_policy,
            request_parameter_policy=dict(request_policy) if isinstance(request_policy, dict) else {},
        )
    return profiles


def _aggregate_order(risk_adapter_cfg: dict[str, Any], detectors: tuple[RiskDetectorSettings, ...]) -> tuple[str, ...]:
    aggregate_cfg = risk_adapter_cfg.get("aggregate", {}) if isinstance(risk_adapter_cfg.get("aggregate", {}), dict) else {}
    order = aggregate_cfg.get("detector_order")
    if order is None:
        order = risk_adapter_cfg.get("detector_order")
    if order is None:
        order = [detector.key for detector in detectors if detector.enabled]
    enabled = {detector.key for detector in detectors if detector.enabled}
    return tuple(str(item) for item in order if str(item) in enabled)


def load_settings(root: Path | None = None, env_file: Path | str | None = None) -> AppSettings:
    project_root = _resolve_project_root(root)
    # 저장소의 .env 기본값은 local/source-tree 실행에서만 사용한다. APP_ENV가
    # production/staging 등으로 명시적으로 export된 경우, secret은 로컬 파일에서
    # 조용히 채워지는 대신 반드시 process environment에서 와야 한다.
    load_local_dotenv_when_allowed(project_root, env_file)

    model_serving = load_yaml_mapping(project_root / "configs" / "model_serving.yaml")
    model_catalog = load_yaml_mapping(project_root / "configs" / "model_catalog.yaml")
    main_model_catalog = load_main_model_catalog(
        project_root / "configs" / "main_model_profiles.yaml",
        env=dict(os.environ),
    )
    version = (project_root / "VERSION").read_text(encoding="utf-8").strip()

    security_cfg = model_serving.get("security", {})
    timeouts = model_serving.get("timeouts", {})
    operational_limits = model_serving.get("operational_limits", {})
    documentation_cfg = model_serving.get("documentation", {})
    models = model_serving["models"]

    documentation = _documentation_settings(documentation_cfg)
    cors = _cors_settings()
    app_env = _env("APP_ENV", "local")
    security = build_security_settings(app_env=app_env, security_cfg=security_cfg)

    vllm_timeout = _as_float("VLLM_TIMEOUT_SECONDS", float(timeouts.get("vllm_request_seconds", 20)), minimum=0.1)
    gateway_timeout_seconds = _as_float(
        "REQUEST_TIMEOUT_SECONDS",
        float(timeouts.get("gateway_request_seconds", 125)),
        minimum=0.1,
    )
    risk_adapter_timeout_seconds = _as_float(
        "RISK_ADAPTER_TIMEOUT_SECONDS",
        float(timeouts.get("risk_adapter_seconds", 15)),
        minimum=0.1,
    )

    runtime_endpoints = _build_runtime_endpoints(
        models=models,
        timeout=vllm_timeout,
        operational_limits=operational_limits,
    )
    embedding_profiles = _embedding_profiles_from_config(model_serving)
    risk_adapter_cfg = model_serving.get("risk_adapter")
    if not isinstance(risk_adapter_cfg, dict):
        raise RuntimeError("risk_adapter must be configured in configs/model_serving.yaml")
    risk_detectors = _risk_detectors_from_config(risk_adapter_cfg)
    aggregate_detector_order = _aggregate_order(risk_adapter_cfg, risk_detectors)
    main_llm = runtime_endpoints["main_llm"]
    # timeout budget에는 vLLM detector만 반영된다; local detector는 in-process로 실행된다.
    risk_detector_endpoints = tuple(
        runtime_endpoints[detector.service_key]
        for detector in risk_detectors
        if (
            detector.enabled
            and detector.key in aggregate_detector_order
            and detector.detector_type != "local"
            and detector.service_key in runtime_endpoints
        )
    )

    risk_adapter_execution = str(risk_adapter_cfg.get("aggregate", {}).get("execution", risk_adapter_cfg.get("aggregate_execution", "sequential")))
    validate_timeout_budget(
        gateway_timeout_seconds=gateway_timeout_seconds,
        risk_adapter_timeout_seconds=risk_adapter_timeout_seconds,
        main_llm=main_llm,
        risk_detectors=risk_detector_endpoints,
        risk_adapter_execution=risk_adapter_execution,
    )

    risk_input_policy = risk_adapter_cfg.get("input_policy", {})
    detector_windows = [endpoint.max_model_len for endpoint in risk_detector_endpoints]
    if any(window is None for window in detector_windows):
        raise RuntimeError("Each enabled vLLM risk detector must declare max_model_len in configs/model_serving.yaml.")
    if detector_windows:
        detector_context_chars = detector_prompt_char_budget(min(int(window) for window in detector_windows))
    else:
        detector_context_chars = _as_int("RISK_INPUT_MAX_CHARS", int(risk_input_policy["max_prompt_chars"]), minimum=1)
    risk_input_max_chars = _as_int(
        "RISK_INPUT_MAX_CHARS",
        int(risk_input_policy.get("max_prompt_chars", detector_context_chars)),
        minimum=1,
    )

    streaming_cfg = model_serving.get("streaming", {})

    return AppSettings(
        app_env=app_env,
        project_version=_env("PROJECT_VERSION", version),
        security=security,
        gateway_timeout_seconds=gateway_timeout_seconds,
        risk_adapter_timeout_seconds=risk_adapter_timeout_seconds,
        risk_adapter_base_url=_env("RISK_ADAPTER_BASE_URL", str(risk_adapter_cfg["endpoint"])).rstrip("/"),
        runtime_endpoints=runtime_endpoints,
        risk_detectors=risk_detectors,
        aggregate_detector_order=aggregate_detector_order,
        default_main_model_gateway_policy=dict(
            main_model_catalog.profiles[main_model_catalog.default_profile].gateway_policy
        ),
        main_model_profile_summaries=tuple(
            (profile.profile_id, profile.display_name, str(profile.compatibility.get("status", "")))
            for profile in main_model_catalog.profiles.values()
        ),
        embedding_profiles=embedding_profiles,
        default_embedding_model=str(model_serving["default_embedding_model"]),
        default_retrieval_model=str(model_serving["default_retrieval_model"]),
        max_request_body_bytes=_as_int(
            "MAX_REQUEST_BODY_BYTES",
            int(operational_limits.get("max_request_body_bytes", 100_000_000)),
        ),
        max_retrieval_documents=int(operational_limits.get("max_retrieval_documents", 32)),
        risk_input_max_chars=risk_input_max_chars,
        public_models=_public_models_from_registry(
            model_catalog,
            model_serving,
            main_model_catalog.profiles[main_model_catalog.default_profile].gateway_policy,
        ),
        documentation=documentation,
        cors=cors,
        readiness_probe_timeout_seconds=float(operational_limits.get("readiness_probe_timeout_seconds", 2.0)),
        streaming_max_duration_seconds=float(streaming_cfg.get("max_duration_seconds", 300.0)),
        streaming_max_chunks=int(streaming_cfg.get("max_chunks", 20_000)),
        streaming_max_bytes=int(streaming_cfg.get("max_bytes", 104_857_600)),
        admin_sidecar_url=_env("ADMIN_SIDECAR_URL", ""),
        deploy_release_id=_env("DEPLOY_RELEASE_ID", ""),
        log_request_response_body=_as_bool(_env("LOG_REQUEST_RESPONSE_BODY", "false"), False),
    )
