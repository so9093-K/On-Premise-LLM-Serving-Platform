"""Drift guards for the projected vLLM runtime topology."""

from __future__ import annotations

import importlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(rel: str) -> dict:
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def _vllm_services() -> dict[str, str]:
    """logical model key -> compose service name, for every vLLM model."""
    cfg = _load_yaml("configs/model_serving.yaml")
    services: dict[str, str] = {}
    for key, model in (cfg.get("models") or {}).items():
        if model.get("backend") != "vllm" or not model.get("endpoint"):
            continue
        services[key] = model["endpoint"].split("//", 1)[1].split(":")[0]
    return services


def _controllable() -> dict[str, str]:
    """Single source of truth: every vLLM model except the main runtime."""
    main_service = str(_load_yaml("configs/main_model_profiles.yaml")["runtime"]["compose_service"])
    return {key: svc for key, svc in _vllm_services().items() if svc != main_service}


def _sidecar(monkeypatch):
    monkeypatch.setenv("APP_CONFIG_ROOT", str(ROOT))
    monkeypatch.setenv("MAIN_MODEL_STATE_PATH", "/tmp/_topology_state.json")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "")
    import ai_model_serving.apps.admin_sidecar as sidecar

    return importlib.reload(sidecar)


def test_sidecar_controllable_matches_model_serving(monkeypatch):
    sidecar = _sidecar(monkeypatch)
    assert set(sidecar.CONTROLLABLE) == set(_controllable().values())


def test_gateway_controllable_keys_match_model_serving():
    from ai_model_serving.runtime_topology import load_runtime_topology

    assert set(load_runtime_topology(ROOT).controllable_keys) == set(_controllable())


def test_controllable_key_and_service_views_are_one_to_one(monkeypatch):
    # The gateway (logical keys) and the sidecar (compose service names) must
    # describe the same set of runtimes, just in their own naming domain.
    sidecar = _sidecar(monkeypatch)
    from ai_model_serving.runtime_topology import load_runtime_topology

    controllable = _controllable()
    assert set(load_runtime_topology(ROOT).controllable_keys) == set(controllable)
    assert set(sidecar.CONTROLLABLE) == set(controllable.values())


def test_start_prerequisites_match_compose_secondary_edges(monkeypatch):
    sidecar = _sidecar(monkeypatch)
    from ai_model_serving.runtime_topology import load_runtime_topology

    controllable = set(_controllable().values())
    compose = _load_yaml("ops/compose/full-stack.private-network.yaml")

    expected: dict[str, list[str]] = {}
    for service in controllable:
        depends = (compose["services"].get(service) or {}).get("depends_on") or {}
        names = depends.keys() if isinstance(depends, dict) else depends
        # Only secondary<->secondary edges. The root edge to main-llm-vllm exists
        # in compose for cold-boot serialization, but the runtime sequencer omits
        # it on purpose: under GPU admission the main model may be deliberately
        # stopped, so starting a secondary must not wait on main health.
        prereqs = sorted(n for n in names if n in controllable)
        if prereqs:
            expected[service] = prereqs

    actual = {service: sorted(prereqs) for service, prereqs in sidecar._START_PREREQUISITES.items()}
    assert actual == expected
    projected = load_runtime_topology(
        ROOT,
        compose_path=ROOT / "ops/compose/full-stack.private-network.yaml",
    )
    assert {
        service: sorted(prereqs)
        for service, prereqs in projected.start_prerequisites_by_service.items()
    } == expected
