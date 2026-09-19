from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_risk_signal_service_uses_canonical_repository_config_namespace() -> None:
    model_serving = _load_yaml("configs/model_serving.yaml")
    services = _load_yaml("configs/services.yaml")
    monitoring = _load_yaml("configs/monitoring.yaml")

    assert "risk_signal_service" in model_serving
    assert "risk_signal_service" in services["services"]
    assert "risk_signal_service" in monitoring["services"]

    assert "risk_adapter" not in model_serving
    assert "risk_adapter" not in services["services"]
    assert "risk_adapter" not in monitoring["services"]
