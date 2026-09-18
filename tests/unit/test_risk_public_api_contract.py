from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _openapi_paths(name: str) -> set[str]:
    document = yaml.safe_load((ROOT / "specs" / name).read_text(encoding="utf-8"))
    return set(document["paths"])


def test_risk_signal_service_public_routes_remain_risk_domain_contract() -> None:
    paths = _openapi_paths("openapi.risk-signal-service.yaml")

    public_paths = {path for path in paths if path.startswith("/v1/")}
    assert public_paths
    assert all(path.startswith("/v1/risk/") for path in public_paths)


def test_gateway_and_risk_signal_service_share_public_risk_route_namespace() -> None:
    gateway_paths = _openapi_paths("openapi.gateway.yaml")
    risk_service_paths = _openapi_paths("openapi.risk-signal-service.yaml")

    gateway_risk_paths = {path for path in gateway_paths if path.startswith("/v1/risk/")}
    service_risk_paths = {path for path in risk_service_paths if path.startswith("/v1/risk/")}

    assert gateway_risk_paths
    assert gateway_risk_paths == service_risk_paths
