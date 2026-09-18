from __future__ import annotations

from scripts.config import validate_image_refs


def _specs() -> dict[str, dict[str, object]]:
    return {
        "prometheus": {
            "env_key": "PROMETHEUS_IMAGE",
            "reference_policy": "immutable_upstream",
            "default": "prom/prometheus:v3-distroless@sha256:" + "a" * 64,
        },
        "platform": {
            "env_key": "PLATFORM_IMAGE",
            "reference_policy": "local_build",
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


def test_image_contract_uses_declared_env_key_instead_of_name_projection():
    specs = {
        "metrics_backend": {
            "env_key": "PROMETHEUS_IMAGE",
            "reference_policy": "immutable_upstream",
            "default": "prom/prometheus@sha256:" + "a" * 64,
        }
    }

    errors = validate_image_refs.remote_env_errors(
        {"PROMETHEUS_IMAGE": "registry.example/prometheus@sha256:" + "b" * 64},
        specs=specs,
    )

    assert errors == []


def test_image_contract_rejects_duplicate_env_key():
    specs = _specs()
    specs["other"] = {
        "env_key": "PROMETHEUS_IMAGE",
        "reference_policy": "local_build",
        "default": "example/other:dev",
    }

    _entries, errors = validate_image_refs._validated_entries(specs)

    assert "recommended image env_key must be unique: PROMETHEUS_IMAGE" in errors


def test_image_contract_rejects_unknown_reference_policy():
    specs = _specs()
    specs["platform"]["reference_policy"] = "mutable"

    _entries, errors = validate_image_refs._validated_entries(specs)

    assert any("reference_policy must be one of" in error for error in errors)


def test_local_build_policy_allows_mutable_local_tag():
    _entries, errors = validate_image_refs._validated_entries(_specs())

    assert errors == []
