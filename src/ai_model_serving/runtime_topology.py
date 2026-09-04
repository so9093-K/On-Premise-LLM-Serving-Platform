from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .configuration import load_yaml_mapping
from .deployment_target import KNOWN_FEATURES


@dataclass(frozen=True)
class RuntimeBinding:
    key: str
    service_id: str
    compose_service: str
    features: frozenset[str]
    required: bool
    enabled: bool
    controllable: bool


@dataclass(frozen=True)
class RuntimeTopology:
    bindings_by_key: dict[str, RuntimeBinding]
    health_port_by_service: dict[str, int]
    vram_fraction_by_service: dict[str, float]
    criticality_by_service: dict[str, str]
    start_prerequisites_by_service: dict[str, list[str]]

    @property
    def service_by_key(self) -> dict[str, str]:
        """Sidecar가 제어할 수 있는 runtime의 Compose projection."""
        return {
            key: binding.compose_service
            for key, binding in self.bindings_by_key.items()
            if binding.enabled and binding.controllable
        }

    @property
    def controllable_keys(self) -> frozenset[str]:
        return frozenset(self.service_by_key)

    @property
    def controllable_services(self) -> frozenset[str]:
        return frozenset(self.service_by_key.values())

    def runtime_keys_for_features(self, features: frozenset[str]) -> frozenset[str]:
        return frozenset(
            key
            for key, binding in self.bindings_by_key.items()
            if binding.enabled and bool(binding.features & features)
        )

    def required_keys_for_features(self, features: frozenset[str]) -> frozenset[str]:
        return frozenset(
            key
            for key, binding in self.bindings_by_key.items()
            if binding.enabled and binding.required and bool(binding.features & features)
        )


def load_runtime_topology(config_root: Path, *, compose_path: Path | None = None) -> RuntimeTopology:
    model_serving = load_yaml_mapping(config_root / "configs/model_serving.yaml")
    services_document = load_yaml_mapping(config_root / "configs/services.yaml")
    topology_document = load_yaml_mapping(config_root / "configs/runtime_topology.yaml")
    bindings = topology_document.get("runtimes")
    services = services_document.get("services")
    if not isinstance(bindings, dict):
        raise ValueError("runtime_topology.yaml runtimes must be a mapping")
    if not isinstance(services, dict):
        raise ValueError("services.yaml services must be a mapping")

    bindings_by_key: dict[str, RuntimeBinding] = {}
    health_port_by_service: dict[str, int] = {}
    vram_fraction_by_service: dict[str, float] = {}
    criticality_by_service: dict[str, str] = {}
    models = model_serving.get("models") or {}
    for key, raw_binding in bindings.items():
        if not isinstance(raw_binding, dict):
            raise ValueError(f"runtime topology binding {key!r} must be a mapping")
        model = models.get(key)
        if not isinstance(model, dict):
            raise ValueError(f"runtime topology binding {key!r} has no model_serving entry")
        service_id = str(raw_binding.get("service_id", ""))
        service_cfg = services.get(service_id)
        if not service_id or not isinstance(service_cfg, dict):
            raise ValueError(
                f"runtime topology binding {key!r} references unknown service_id {service_id!r}"
            )
        compose_service = str(service_cfg.get("compose_service", ""))
        if not compose_service:
            raise ValueError(f"services.yaml service {service_id!r} requires compose_service")
        if int(service_cfg.get("container_port", -1)) != int(model.get("port", -2)):
            raise ValueError(
                f"runtime topology binding {key!r} service_id {service_id!r} port "
                "does not match model_serving"
            )
        raw_features = raw_binding.get("features")
        if (
            not isinstance(raw_features, list)
            or not raw_features
            or not all(isinstance(item, str) and item for item in raw_features)
        ):
            raise ValueError(
                f"runtime topology binding {key!r} features must be a non-empty string list"
            )
        unknown_features = set(raw_features) - KNOWN_FEATURES
        if unknown_features:
            raise ValueError(
                f"runtime topology binding {key!r} references unknown features: "
                f"{', '.join(sorted(unknown_features))}"
            )
        for flag in ("required", "enabled", "controllable"):
            if not isinstance(raw_binding.get(flag), bool):
                raise ValueError(f"runtime topology binding {key!r}.{flag} must be boolean")
        if not raw_binding["enabled"] and (
            raw_binding["required"] or raw_binding["controllable"]
        ):
            raise ValueError(
                f"disabled runtime topology binding {key!r} cannot be required or controllable"
            )

        binding = RuntimeBinding(
            key=str(key),
            service_id=service_id,
            compose_service=compose_service,
            features=frozenset(raw_features),
            required=bool(raw_binding["required"]),
            enabled=bool(raw_binding["enabled"]),
            controllable=bool(raw_binding["controllable"]),
        )
        bindings_by_key[str(key)] = binding
        if binding.enabled and binding.controllable:
            if model.get("port") is not None:
                health_port_by_service[compose_service] = int(model["port"])
            if model.get("gpu_memory_utilization") is not None:
                vram_fraction_by_service[compose_service] = float(
                    model["gpu_memory_utilization"]
                )
            criticality = ((model.get("resource_control") or {}).get("criticality") or "")
            criticality_by_service[compose_service] = str(criticality)

    start_prerequisites_by_service: dict[str, list[str]] = {}
    if compose_path is not None and compose_path.exists():
        compose = load_yaml_mapping(compose_path)
        controllable = {
            binding.compose_service
            for binding in bindings_by_key.values()
            if binding.enabled and binding.controllable
        }
        for service in controllable:
            depends = ((compose.get("services") or {}).get(service) or {}).get("depends_on") or {}
            names = depends.keys() if isinstance(depends, dict) else depends
            prereqs = [str(name) for name in names if str(name) in controllable]
            if prereqs:
                start_prerequisites_by_service[service] = prereqs

    return RuntimeTopology(
        bindings_by_key=bindings_by_key,
        health_port_by_service=health_port_by_service,
        vram_fraction_by_service=vram_fraction_by_service,
        criticality_by_service=criticality_by_service,
        start_prerequisites_by_service=start_prerequisites_by_service,
    )
