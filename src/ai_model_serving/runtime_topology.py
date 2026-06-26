from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


@dataclass(frozen=True)
class RuntimeTopology:
    main_service: str
    service_by_key: dict[str, str]
    health_port_by_service: dict[str, int]
    vram_fraction_by_service: dict[str, float]
    criticality_by_service: dict[str, str]
    start_prerequisites_by_service: dict[str, list[str]]

    @property
    def controllable_keys(self) -> frozenset[str]:
        return frozenset(self.service_by_key)

    @property
    def controllable_services(self) -> frozenset[str]:
        return frozenset(self.service_by_key.values())


def service_from_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    service = parsed.hostname
    if not service:
        raise ValueError(f"runtime endpoint must include a host: {endpoint}")
    return service


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_runtime_topology(config_root: Path, *, compose_path: Path | None = None) -> RuntimeTopology:
    model_serving = _load_yaml(config_root / "configs/model_serving.yaml")
    main_profiles = _load_yaml(config_root / "configs/main_model_profiles.yaml")
    main_service = str((main_profiles.get("runtime") or {}).get("compose_service", ""))
    if not main_service:
        raise ValueError("main_model_profiles.yaml runtime.compose_service is required")

    service_by_key: dict[str, str] = {}
    health_port_by_service: dict[str, int] = {}
    vram_fraction_by_service: dict[str, float] = {}
    criticality_by_service: dict[str, str] = {}
    for key, model in (model_serving.get("models") or {}).items():
        if model.get("backend") != "vllm" or not model.get("endpoint"):
            continue
        service = service_from_endpoint(str(model["endpoint"]))
        if service == main_service:
            continue
        service_by_key[str(key)] = service
        if model.get("port") is not None:
            health_port_by_service[service] = int(model["port"])
        if model.get("gpu_memory_utilization") is not None:
            vram_fraction_by_service[service] = float(model["gpu_memory_utilization"])
        criticality = ((model.get("resource_control") or {}).get("criticality") or "")
        criticality_by_service[service] = str(criticality)

    start_prerequisites_by_service: dict[str, list[str]] = {}
    if compose_path is not None and compose_path.exists():
        compose = _load_yaml(compose_path)
        controllable = set(service_by_key.values())
        for service in controllable:
            depends = ((compose.get("services") or {}).get(service) or {}).get("depends_on") or {}
            names = depends.keys() if isinstance(depends, dict) else depends
            prereqs = [str(name) for name in names if str(name) in controllable]
            if prereqs:
                start_prerequisites_by_service[service] = prereqs

    return RuntimeTopology(
        main_service=main_service,
        service_by_key=service_by_key,
        health_port_by_service=health_port_by_service,
        vram_fraction_by_service=vram_fraction_by_service,
        criticality_by_service=criticality_by_service,
        start_prerequisites_by_service=start_prerequisites_by_service,
    )
