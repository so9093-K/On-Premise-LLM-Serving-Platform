"""projected vLLM runtime topology에 대한 drift 가드."""

from __future__ import annotations

import importlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(rel: str) -> dict:
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def _vllm_services() -> dict[str, str]:
    """모든 vLLM 모델에 대해 logical model key -> compose service name."""
    cfg = _load_yaml("configs/model_serving.yaml")
    services: dict[str, str] = {}
    for key, model in (cfg.get("models") or {}).items():
        if model.get("backend") != "vllm" or not model.get("endpoint"):
            continue
        services[key] = model["endpoint"].split("//", 1)[1].split(":")[0]
    return services


def _controllable() -> dict[str, str]:
    """단일 source of truth: main runtime을 제외한 모든 vLLM 모델."""
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


def test_start_prerequisites_match_compose_secondary_edges(monkeypatch):
    sidecar = _sidecar(monkeypatch)
    from ai_model_serving.runtime_topology import load_runtime_topology

    controllable = set(_controllable().values())
    compose = _load_yaml("ops/compose/full-stack.private-network.yaml")

    expected: dict[str, list[str]] = {}
    for service in controllable:
        depends = (compose["services"].get(service) or {}).get("depends_on") or {}
        names = depends.keys() if isinstance(depends, dict) else depends
        # secondary<->secondary edge만 대상이다. main-llm-vllm으로 가는 root edge는
        # compose엔 cold-boot 직렬화를 위해 존재하지만, runtime sequencer는 이를
        # 의도적으로 빼둔다: GPU admission 하에서는 main model이 의도적으로
        # 중지될 수 있으므로, secondary 시작이 main health를 기다리면 안 된다.
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
