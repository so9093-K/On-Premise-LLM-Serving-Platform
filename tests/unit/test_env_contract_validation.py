from __future__ import annotations

from scripts.validation.validate_env_contract import (
    _commented_assignment_keys,
    validate_contract_structure,
    validate_service_env_projections,
)


def test_commented_assignment_exposes_removed_env_surface(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text(
        "# MAX_REQUEST_BODY_BYTES=<bytes>\n"
        "# MAX_REQUEST_BODY_BYTES is owned by model_serving.yaml\n",
        encoding="utf-8",
    )

    assert _commented_assignment_keys(example) == {"MAX_REQUEST_BODY_BYTES"}


def test_service_projection_rejects_removed_persistent_key(tmp_path):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "deployment_targets.yaml").write_text(
        "targets:\n"
        "  static-target:\n"
        "    internal_service_token_required: false\n",
        encoding="utf-8",
    )
    contract = {
        "removed_keys": {"MAX_REQUEST_BODY_BYTES": "model_serving.yaml owns it"},
        "service_env_projections": {
            "static_gateway": {
                "deployment_targets": ["static-target"],
                "required_source_keys": ["DEPLOYMENT_TARGET"],
                "runtime_keys": ["DEPLOYMENT_TARGET", "MAX_REQUEST_BODY_BYTES"],
            }
        },
    }

    violations = validate_service_env_projections(tmp_path, contract)

    assert any(
        "runtime_keys contains removed persistent key(s): MAX_REQUEST_BODY_BYTES" in violation
        for violation in violations
    )


def test_renamed_key_contract_rejects_self_mapping():
    violations = validate_contract_structure(
        {
            "common_example_keys": ["APP_ENV"],
            "auth_mode_keys": ["AUTH_MODE"],
            "auth_evidence_keys": ["CUSTOM_AUTH_RISK_ACCEPTED"],
            "runtime_override_example_keys": {
                "main": {"env_prefix": "MAIN", "suffixes": ["MODEL"]}
            },
            "env_examples": {
                ".env.example": {"required_key_sets": ["common_example_keys"]}
            },
            "renamed_keys": {"OLD_KEY": "OLD_KEY"},
        }
    )

    assert "env_contract.yaml: renamed key 'OLD_KEY' cannot map to itself" in violations
