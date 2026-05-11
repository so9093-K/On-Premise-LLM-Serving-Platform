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
from .settings_parts.types import AppSettings, DocumentationSettings, RuntimeEndpoint, SecuritySettings


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

    main_llm = build_runtime_endpoint(model_key="main_llm", env_url="MAIN_LLM_BASE_URL", env_model="MAIN_LLM_MODEL", timeout=vllm_timeout, models=models, operational_limits=operational_limits)
    embedding = build_runtime_endpoint(model_key="embedding", env_url="EMBEDDING_BASE_URL", env_model="EMBEDDING_MODEL", timeout=vllm_timeout, models=models, operational_limits=operational_limits)
    risk_prompt = build_runtime_endpoint(model_key="risk_prompt", env_url="RISK_PROMPT_BASE_URL", env_model="RISK_PROMPT_MODEL", timeout=vllm_timeout, models=models, operational_limits=operational_limits)
    risk_siren = build_runtime_endpoint(model_key="risk_siren", env_url="RISK_SIREN_BASE_URL", env_model="RISK_SIREN_MODEL", timeout=vllm_timeout, models=models, operational_limits=operational_limits)

    risk_adapter_execution = str(model_serving.get("risk_adapter", {}).get("aggregate_execution", "sequential"))
    validate_timeout_budget(
        gateway_timeout_seconds=gateway_timeout_seconds,
        risk_adapter_timeout_seconds=risk_adapter_timeout_seconds,
        main_llm=main_llm,
        risk_prompt=risk_prompt,
        risk_siren=risk_siren,
        risk_adapter_execution=risk_adapter_execution,
    )

    risk_input_policy = model_serving.get("risk_adapter", {}).get("input_policy", {})
    detector_context_chars = max(1, (min(risk_prompt.max_model_len or 2048, risk_siren.max_model_len or 2048) - 64) * 4)
    risk_input_max_chars = _as_int(
        "RISK_INPUT_MAX_CHARS",
        int(risk_input_policy.get("max_prompt_chars", detector_context_chars)),
        minimum=1,
    )

    return AppSettings(
        app_env=app_env,
        project_version=_env("PROJECT_VERSION", version),
        security=security,
        gateway_timeout_seconds=gateway_timeout_seconds,
        risk_adapter_timeout_seconds=risk_adapter_timeout_seconds,
        main_llm=main_llm,
        embedding=embedding,
        risk_prompt=risk_prompt,
        risk_siren=risk_siren,
        risk_adapter_base_url=_env("RISK_ADAPTER_BASE_URL", str(model_serving["risk_adapter"]["endpoint"])).rstrip("/"),
        max_request_body_bytes=_as_int(
            "MAX_REQUEST_BODY_BYTES",
            int(operational_limits.get("max_request_body_bytes", 1_000_000)),
        ),
        risk_input_max_chars=risk_input_max_chars,
        public_models=_public_models_from_registry(model_catalog, model_serving),
        documentation=documentation,
    )
