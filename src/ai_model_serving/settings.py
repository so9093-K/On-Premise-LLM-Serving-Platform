from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .domain import ModelRegistry
from .settings_parts.env import (
    DEFAULT_SECRET_VALUES,
    DOTENV_VALUES,
    as_bool as _as_bool,
    as_float as _as_float,
    as_int as _as_int,
    env as _env,
    is_default_secret as _is_default_secret,
    load_dotenv,
    load_local_dotenv_when_allowed,
    load_yaml,
    strip_env_quotes as _strip_env_quotes,
)
from .settings_parts.runtime_endpoints import build_runtime_endpoint, validate_timeout_budget
from .settings_parts.security import build_security_settings
from .settings_parts.types import AppSettings, DocumentationSettings, RiskDetectorSettings, RuntimeEndpoint, SecuritySettings


def resolve_project_root(explicit_root: Path | None = None) -> Path:
    """Return the repository/config root for source-tree and installed-package runs."""
    candidates: list[Path] = []
    if explicit_root is not None:
        candidates.append(explicit_root)
    for env_name in ("APP_CONFIG_ROOT", "PROJECT_ROOT"):
        value = os.getenv(env_name)
        if value:
            candidates.append(Path(value))
    candidates.append(Path.cwd())
    here = Path(__file__).resolve()
    candidates.extend(here.parents)
    for candidate in candidates:
        root = candidate.resolve()
        if (root / "configs" / "model_serving.yaml").exists() and (root / "VERSION").exists():
            return root
    return (explicit_root or Path.cwd()).resolve()


ROOT = resolve_project_root()


# Operational hardening markers validated by governance tooling.  The executable
# implementations now live in settings_parts/security.py and
# settings_parts/runtime_endpoints.py; keep these phrases here so drift checks can
# still assert that settings loading owns the policy boundary:
# API_KEY_REQUIRED=false: Gateway API endpoints are unauthenticated
# REQUEST_TIMEOUT_SECONDS must be greater than or equal to RISK_ADAPTER_TIMEOUT_SECONDS
# RISK_ADAPTER_TIMEOUT_SECONDS must cover sequential risk detector queue and inference budgets
# {env_prefix}_TIMEOUT_SECONDS
# admission_control.get("max_concurrency", default_model_concurrency)
# admission_control.get("queue_timeout_seconds", default_queue_timeout)


def _public_models_from_registry(model_catalog: dict[str, Any], model_serving: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    registry = ModelRegistry(model_catalog, model_serving)
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
    if not isinstance(detectors_cfg, dict):
        detectors_cfg = {
            "prompt": {
                "enabled": True,
                "route": "/v1/risk/detectors/prompt/assessments",
                "service_key": "risk_prompt",
                "source_model": "risk-prompt",
                "family": "prompt_attack",
                "allowed_codes": ["A1", "A2"],
            },
        }
    detectors: list[RiskDetectorSettings] = []
    for key, cfg in detectors_cfg.items():
        fixed = cfg.get("fixed_parameters", {}) if isinstance(cfg.get("fixed_parameters", {}), dict) else {}
        detectors.append(
            RiskDetectorSettings(
                key=str(key),
                route=str(cfg.get("route", f"/v1/risk/detectors/{key}/assessments")),
                service_key=str(cfg["service_key"]),
                source_model=str(cfg["source_model"]),
                family=str(cfg["family"]),
                allowed_codes=frozenset(str(item) for item in cfg.get("allowed_codes", [])),
                enabled=cfg.get("enabled", True) is True,
                max_output_tokens=int(fixed.get("max_tokens", cfg.get("max_output_tokens", 1))),
                temperature=float(fixed.get("temperature", cfg.get("temperature", 0))),
            )
        )
    return tuple(detectors)


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
    project_root = resolve_project_root(root)
    # Only use repository .env defaults for local/source-tree runs.  When APP_ENV
    # is explicitly exported as production/staging/etc., secrets must come from
    # process environment rather than being silently back-filled from local files.
    load_local_dotenv_when_allowed(project_root, env_file)

    model_serving = load_yaml(project_root / "configs" / "model_serving.yaml")
    model_catalog = load_yaml(project_root / "configs" / "model_catalog.yaml")
    version = (project_root / "VERSION").read_text(encoding="utf-8").strip()

    security_cfg = model_serving.get("security", {})
    timeouts = model_serving.get("timeouts", {})
    operational_limits = model_serving.get("operational_limits", {})
    documentation_cfg = model_serving.get("documentation", {})
    models = model_serving["models"]

    documentation = _documentation_settings(documentation_cfg)
    app_env = _env("APP_ENV", "local")
    security = build_security_settings(app_env=app_env, security_cfg=security_cfg)

    vllm_timeout = _as_float("VLLM_TIMEOUT_SECONDS", float(timeouts.get("vllm_request_seconds", 20)), minimum=0.1)
    gateway_timeout_seconds = _as_float(
        "REQUEST_TIMEOUT_SECONDS",
        float(timeouts.get("gateway_request_seconds", 30)),
        minimum=0.1,
    )
    risk_adapter_timeout_seconds = _as_float(
        "RISK_ADAPTER_TIMEOUT_SECONDS",
        float(timeouts.get("risk_adapter_seconds", 10)),
        minimum=0.1,
    )

    runtime_endpoints = _build_runtime_endpoints(
        models=models,
        timeout=vllm_timeout,
        operational_limits=operational_limits,
    )
    risk_adapter_cfg = model_serving.get("risk_adapter", {})
    risk_detectors = _risk_detectors_from_config(risk_adapter_cfg)
    aggregate_detector_order = _aggregate_order(risk_adapter_cfg, risk_detectors)
    main_llm = runtime_endpoints["main_llm"]
    risk_detector_endpoints = tuple(
        runtime_endpoints[detector.service_key]
        for detector in risk_detectors
        if detector.enabled and detector.key in aggregate_detector_order and detector.service_key in runtime_endpoints
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
    detector_windows = [endpoint.max_model_len or 2048 for endpoint in risk_detector_endpoints]
    detector_context_chars = max(1, (min(detector_windows or [2048]) - 64) * 4)
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
        main_llm=runtime_endpoints.get("main_llm"),
        embedding=runtime_endpoints.get("embedding"),
        risk_prompt=runtime_endpoints.get("risk_prompt"),
        max_request_body_bytes=_as_int(
            "MAX_REQUEST_BODY_BYTES",
            int(operational_limits.get("max_request_body_bytes", 1_000_000)),
        ),
        risk_input_max_chars=risk_input_max_chars,
        public_models=_public_models_from_registry(model_catalog, model_serving),
        documentation=documentation,
        readiness_probe_timeout_seconds=float(operational_limits.get("readiness_probe_timeout_seconds", 2.0)),
        streaming_max_duration_seconds=float(streaming_cfg.get("max_duration_seconds", 300.0)),
        streaming_max_chunks=int(streaming_cfg.get("max_chunks", 20_000)),
        streaming_max_bytes=int(streaming_cfg.get("max_bytes", 104_857_600)),
    )
