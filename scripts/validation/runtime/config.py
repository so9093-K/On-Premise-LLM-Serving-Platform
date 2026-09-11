from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_model_serving.configuration import load_yaml_mapping
from ai_model_serving.domain import ModelRegistry
from scripts.lib.process_env import load_dotenv
from scripts.lib.service_endpoint import published_base_url, service_base_url


def _explicit_arg(args: Any, name: str) -> str:
    value = getattr(args, name, None)
    return str(value).strip() if value is not None and str(value).strip() else ""


def _url_value(args: Any, attr: str, env_name: str, default: str) -> str:
    """운영자가 명시한 값을 우선해 runtime validation endpoint를 결정한다.

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
    services: dict[str, Any]
    version: str


def _first_csv_value(value: str) -> str:
    values = [item.strip() for item in value.split(",") if item.strip()]
    return values[0] if values else ""


def load_runtime_config(args: Any) -> RuntimeValidationConfig:
    root = Path(args.root).resolve()
    load_dotenv(root)
    model_serving = load_yaml_mapping(root / "configs/model_serving.yaml")
    model_catalog = load_yaml_mapping(root / "configs/model_catalog.yaml")
    monitoring = load_yaml_mapping(root / "configs/monitoring.yaml")
    services = load_yaml_mapping(root / "configs/services.yaml")["services"]
    registry = ModelRegistry(model_catalog, model_serving)
    services_by_compose_name = {
        str(service["compose_service"]): service
        for service in services.values()
    }

    def service_base(service_id: str, suffix: str = "") -> str:
        try:
            return published_base_url(services, service_id, suffix)
        except KeyError as exc:
            raise ValueError(f"configs/services.yaml is missing {service_id}") from exc

    def runtime_base(service: Any) -> str:
        try:
            service_config = services_by_compose_name[service.compose_service_name]
        except KeyError as exc:
            raise ValueError(
                "configs/services.yaml has no entry for runtime compose service "
                f"{service.compose_service_name!r}"
            ) from exc
        return service_base_url(service_config, "/v1")

    api_key = args.api_key or os.getenv("API_KEY", "") or _first_csv_value(os.getenv("API_KEYS", ""))
    admin_api_key = args.admin_api_key or os.getenv("ADMIN_API_KEY", "") or _first_csv_value(os.getenv("ADMIN_API_KEYS", ""))
    return RuntimeValidationConfig(
        root=root,
        output_dir=args.output_dir,
        timeout_seconds=args.timeout_seconds,
        allow_failures=args.allow_failures,
        api_key=api_key,
        admin_api_key=admin_api_key,
        internal_service_token=os.getenv("INTERNAL_SERVICE_TOKEN", ""),
        # *_BASE_URL은 application container가 Compose 내부 서비스에 접속하는 주소다.
        # host에서 실행하는 검증이 이를 우선하면 내부 DNS가 해석되지 않아 실패한다.
        gateway_base=_url_value(args, "gateway_base", "RUNTIME_VALIDATION_GATEWAY_BASE_URL", service_base("gateway")),
        risk_base=_url_value(args, "risk_base", "RUNTIME_VALIDATION_RISK_BASE_URL", service_base("risk_adapter")),
        prometheus_base=_url_value(args, "prometheus_base", "RUNTIME_VALIDATION_PROMETHEUS_BASE_URL", service_base("prometheus")),
        grafana_base=_url_value(args, "grafana_base", "RUNTIME_VALIDATION_GRAFANA_BASE_URL", service_base("grafana")),
        grafana_admin_user=_explicit_arg(args, "grafana_user") or os.getenv("GRAFANA_ADMIN_USER", "admin"),
        grafana_admin_password=_explicit_arg(args, "grafana_password") or os.getenv("GRAFANA_ADMIN_PASSWORD", "admin"),
        vllm_bases={
            service.service_key: _url_value(
                args,
                f"{service.service_key}_base",
                f"RUNTIME_VALIDATION_{service.service_key.upper()}_BASE_URL",
                runtime_base(service),
            )
            for service in registry.iter_runtime_services()
        },
        model_serving=model_serving,
        model_catalog=model_catalog,
        monitoring=monitoring,
        services=services,
        version=(root / "VERSION").read_text(encoding="utf-8").strip(),
    )
