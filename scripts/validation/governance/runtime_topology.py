from __future__ import annotations

from ai_model_serving.runtime_topology import load_runtime_topology

from .common import ROOT, read_yaml


def _depends_on_names(raw: object, *, service: str) -> tuple[set[str], dict[str, object] | None]:
    if raw is None:
        return set(), {}
    if isinstance(raw, dict):
        return {str(name) for name in raw}, raw
    if isinstance(raw, list) and all(isinstance(name, str) for name in raw):
        return set(raw), None
    raise SystemExit(
        f"compose service {service!r} depends_on must be a mapping or string list"
    )


def validate_runtime_prerequisite_projection() -> None:
    """Keep Compose startup ordering aligned with Runtime Control policy.

    configs/runtime_topology.yaml owns start prerequisites among controllable
    runtimes. Compose may additionally encode deployment-only dependencies such
    as the initial Main Model boot, but it must not invent or omit a dependency
    between runtimes that Runtime Control can start independently.
    """
    try:
        topology = load_runtime_topology(ROOT)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    compose = read_yaml("ops/compose/full-stack.private-network.yaml")
    services = compose.get("services")
    if not isinstance(services, dict):
        raise SystemExit("full-stack compose must define services")

    controllable = topology.controllable_services
    for service in sorted(controllable):
        service_document = services.get(service)
        if not isinstance(service_document, dict):
            raise SystemExit(
                f"full-stack compose is missing controllable runtime service {service!r}"
            )

        dependency_names, dependency_mapping = _depends_on_names(
            service_document.get("depends_on"),
            service=service,
        )
        actual = dependency_names & controllable
        expected = set(topology.start_prerequisites_by_service.get(service, []))
        if actual != expected:
            raise SystemExit(
                f"runtime prerequisite drift for {service}: "
                f"runtime_topology={sorted(expected)}, compose={sorted(actual)}"
            )

        if expected and dependency_mapping is None:
            raise SystemExit(
                f"runtime prerequisite drift for {service}: controllable dependencies "
                "must use condition: service_healthy in Compose"
            )
        for prerequisite in expected:
            declaration = dependency_mapping.get(prerequisite) if dependency_mapping else None
            if not isinstance(declaration, dict) or declaration.get("condition") != "service_healthy":
                raise SystemExit(
                    f"runtime prerequisite drift for {service}: {prerequisite} must use "
                    "condition: service_healthy in Compose"
                )
