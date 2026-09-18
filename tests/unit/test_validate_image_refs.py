from __future__ import annotations

from scripts.config import validate_image_refs


def _specs() -> dict[str, dict[str, object]]:
    return {
        "prometheus": {
            "default": "prom/prometheus:v3-distroless@sha256:" + "a" * 64,
            "remote_immutable": True,
        },
        "platform": {
            "default": "ai-model-serving-platform:dev",
        },
    }


def test_remote_image_contract_rejects_mutable_third_party_tag():
    errors = validate_image_refs.remote_env_errors(
        {"PROMETHEUS_IMAGE": "prom/prometheus:v3-distroless"},
        specs=_specs(),
    )

    assert errors == [
        "PROMETHEUS_IMAGE (prometheus) must be an immutable registry digest "
        "(name@sha256:<64 lowercase hex>)"
    ]


def test_remote_image_contract_accepts_alternate_immutable_digest():
    errors = validate_image_refs.remote_env_errors(
        {"PROMETHEUS_IMAGE": "registry.example/prometheus@sha256:" + "b" * 64},
        specs=_specs(),
    )

    assert errors == []
