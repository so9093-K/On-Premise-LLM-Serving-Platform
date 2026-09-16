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
    def health_url_by_service(self) -> dict[str, str]:
        """제어 가능한 runtime의 Compose 내부 health endpoint.

        포트가 이미 여기서 나오는데 hostname만 호출부가 f-string으로 붙이고 있었다.
        조립을 topology로 옮기면 "이 서비스의 health를 어떻게 부르는가"가 한 곳에
        모이고, 호출부는 HTTP 경로 파라미터로 URL을 만들 이유가 사라진다.
        """
        return {
            service: f"http://{service}:{port}/health"
            for service, port in self.health_port_by_service.items()
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


def _validate_prerequisite_graph(
    bindings_by_key: dict[str, RuntimeBinding],
    prerequisite_keys_by_key: dict[str, tuple[str, ...]],
) -> None:
    for key, prerequisite_keys in prerequisite_keys_by_key.items():
        binding = bindings_by_key[key]
        if prerequisite_keys and not (binding.enabled and binding.controllable):
            raise ValueError(
                f"runtime topology binding {key!r} cannot declare start_prerequisites "
                "unless it is enabled and controllable"
            )
        for prerequisite_key in prerequisite_keys:
            if prerequisite_key == key:
                raise ValueError(
                    f"runtime topology binding {key!r} cannot depend on itself"
                )
            prerequisite = bindings_by_key.get(prerequisite_key)
            if prerequisite is None:
                raise ValueError(
                    f"runtime topology binding {key!r} references unknown "
                    f"start prerequisite {prerequisite_key!r}"
                )
            if not (prerequisite.enabled and prerequisite.controllable):
                raise ValueError(
                    f"runtime topology binding {key!r} start prerequisite "
                    f"{prerequisite_key!r} must be enabled and controllable"
                )

    visiting: list[str] = []
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visited:
            return
        if key in visiting:
            cycle_start = visiting.index(key)
            cycle = [*visiting[cycle_start:], key]
            raise ValueError(
                "runtime topology start_prerequisites contain a cycle: "
                + " -> ".join(cycle)
            )
        visiting.append(key)
        for prerequisite_key in prerequisite_keys_by_key.get(key, ()):
            visit(prerequisite_key)
        visiting.pop()
        visited.add(key)

    for key in prerequisite_keys_by_key:
        visit(key)


def load_runtime_topology(config_root: Path) -> RuntimeTopology:
    """Load the canonical runtime lifecycle topology.

    ``configs/runtime_topology.yaml`` owns Runtime Control start prerequisites.
    """
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
    prerequisite_keys_by_key: dict[str, tuple[str, ...]] = {}
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

        raw_prerequisites = raw_binding.get("start_prerequisites")
        if raw_binding["controllable"]:
            if (
                not isinstance(raw_prerequisites, list)
                or not all(
                    isinstance(item, str) and item for item in raw_prerequisites
                )
            ):
                raise ValueError(
                    f"runtime topology binding {key!r}.start_prerequisites "
                    "must be a string list for controllable runtimes"
                )
            if len(raw_prerequisites) != len(set(raw_prerequisites)):
                raise ValueError(
                    f"runtime topology binding {key!r}.start_prerequisites "
                    "must not contain duplicates"
                )
            prerequisite_keys_by_key[str(key)] = tuple(raw_prerequisites)
        elif raw_prerequisites is not None:
            raise ValueError(
                f"runtime topology binding {key!r}.start_prerequisites "
                "is only valid for controllable runtimes"
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

    _validate_prerequisite_graph(bindings_by_key, prerequisite_keys_by_key)

    start_prerequisites_by_service = {
        bindings_by_key[key].compose_service: [
            bindings_by_key[prerequisite_key].compose_service
            for prerequisite_key in prerequisite_keys
        ]
        for key, prerequisite_keys in prerequisite_keys_by_key.items()
        if prerequisite_keys
    }

    return RuntimeTopology(
        bindings_by_key=bindings_by_key,
        health_port_by_service=health_port_by_service,
        vram_fraction_by_service=vram_fraction_by_service,
        criticality_by_service=criticality_by_service,
        start_prerequisites_by_service=start_prerequisites_by_service,
    )
