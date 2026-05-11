from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .env import load_dotenv


DEFAULT_BASE_URLS = {
    "gateway": "http://localhost:9400",
    "risk": "http://localhost:9405",
    "main_llm": "http://localhost:9401/v1",
    "embedding": "http://localhost:9402/v1",
    "risk_prompt": "http://localhost:9403/v1",
    "risk_siren": "http://localhost:9404/v1",
    "prometheus": "http://localhost:9410",
    "grafana": "http://localhost:9411",
}


def _explicit_arg(args: Any, name: str) -> str:
    value = getattr(args, name, None)
    return str(value).strip() if value is not None and str(value).strip() else ""


def _url_value(args: Any, attr: str, env_name: str, default: str) -> str:
    """Resolve runtime validation endpoint with explicit operator intent first.

    우선순위는 CLI 인자 > process/.env 환경변수 > built-in 기본값이다.
    ``load_dotenv``는 이미 process env를 덮어쓰지 않으므로 exported env가
    repository ``.env``보다 우선한다.
    """
    return (_explicit_arg(args, attr) or os.getenv(env_name, "") or default).rstrip("/")


@dataclass(frozen=True)
class RuntimeValidationConfig:
    root: Path
    output_dir: str
    timeout_seconds: float
    soak_seconds: int
    soak_interval_seconds: float
    concurrency: int
    skip_soak: bool
    config_only: bool
    allow_failures: bool
    api_key: str
    admin_api_key: str
    internal_service_token: str
    gateway_base: str
    risk_base: str
    prometheus_base: str
    grafana_base: str
    grafana_admin_user: str
    grafana_admin_password: str
    vllm_bases: dict[str, str]
    model_serving: dict[str, Any]
    model_catalog: dict[str, Any]
    monitoring: dict[str, Any]
    gpu_budgets: dict[str, Any]
    version: str


def _first_csv_value(value: str) -> str:
    values = [item.strip() for item in value.split(",") if item.strip()]
    return values[0] if values else ""


def load_runtime_config(args: Any) -> RuntimeValidationConfig:
    root = Path(args.root).resolve()
    load_dotenv(root)
    model_serving = yaml.safe_load((root / "configs/model_serving.yaml").read_text(encoding="utf-8"))
    model_catalog = yaml.safe_load((root / "configs/model_catalog.yaml").read_text(encoding="utf-8"))
    monitoring = yaml.safe_load((root / "configs/monitoring.yaml").read_text(encoding="utf-8"))
    gpu_budgets = yaml.safe_load((root / "configs/gpu_budgets.yaml").read_text(encoding="utf-8"))

    api_key = args.api_key or os.getenv("API_KEY", "") or _first_csv_value(os.getenv("API_KEYS", ""))
    admin_api_key = args.admin_api_key or os.getenv("ADMIN_API_KEY", "") or _first_csv_value(os.getenv("ADMIN_API_KEYS", ""))
    return RuntimeValidationConfig(
        root=root,
        output_dir=args.output_dir,
        timeout_seconds=args.timeout_seconds,
        soak_seconds=args.soak_seconds,
        soak_interval_seconds=args.soak_interval_seconds,
        concurrency=args.concurrency,
        skip_soak=args.skip_soak,
        config_only=args.config_only,
        allow_failures=args.allow_failures,
        api_key=api_key,
        admin_api_key=admin_api_key,
        internal_service_token=os.getenv("INTERNAL_SERVICE_TOKEN", ""),
        gateway_base=_url_value(args, "gateway_base", "GATEWAY_BASE_URL", DEFAULT_BASE_URLS["gateway"]),
        risk_base=_url_value(args, "risk_base", "RISK_ADAPTER_BASE_URL", DEFAULT_BASE_URLS["risk"]),
        prometheus_base=_url_value(args, "prometheus_base", "PROMETHEUS_BASE_URL", DEFAULT_BASE_URLS["prometheus"]),
        grafana_base=_url_value(args, "grafana_base", "GRAFANA_BASE_URL", DEFAULT_BASE_URLS["grafana"]),
        grafana_admin_user=_explicit_arg(args, "grafana_user") or os.getenv("GRAFANA_ADMIN_USER", "admin"),
        grafana_admin_password=_explicit_arg(args, "grafana_password") or os.getenv("GRAFANA_ADMIN_PASSWORD", "admin"),
        vllm_bases={
            "main_llm": _url_value(args, "main_llm_base", "MAIN_LLM_BASE_URL", DEFAULT_BASE_URLS["main_llm"]),
            "embedding": _url_value(args, "embedding_base", "EMBEDDING_BASE_URL", DEFAULT_BASE_URLS["embedding"]),
            "risk_prompt": _url_value(args, "risk_prompt_base", "RISK_PROMPT_BASE_URL", DEFAULT_BASE_URLS["risk_prompt"]),
            "risk_siren": _url_value(args, "risk_siren_base", "RISK_SIREN_BASE_URL", DEFAULT_BASE_URLS["risk_siren"]),
        },
        model_serving=model_serving,
        model_catalog=model_catalog,
        monitoring=monitoring,
        gpu_budgets=gpu_budgets,
        version=(root / "VERSION").read_text(encoding="utf-8").strip(),
    )
