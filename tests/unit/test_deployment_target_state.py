from __future__ import annotations

import pytest

from ai_model_serving.deployment_target_state import validate_deployment_target_state


def test_canonical_deployment_target_state_is_preserved() -> None:
    state = validate_deployment_target_state(
        "canonical-target",
        {
            "implementation_status": "implemented",
            "qualification_status": "verified",
        },
    )

    assert state.implementation_status == "implemented"
    assert state.qualification_status == "verified"


def test_legacy_validation_status_is_rejected() -> None:
    with pytest.raises(ValueError, match="removed legacy validation_status"):
        validate_deployment_target_state(
            "legacy-target",
            {"validation_status": "verified"},
        )


def test_planned_target_cannot_be_qualification_verified() -> None:
    with pytest.raises(ValueError, match="cannot be qualification verified"):
        validate_deployment_target_state(
            "impossible-target",
            {
                "implementation_status": "planned",
                "qualification_status": "verified",
            },
        )


def test_partial_canonical_state_fails_closed() -> None:
    with pytest.raises(ValueError, match="invalid qualification_status"):
        validate_deployment_target_state(
            "partial-target",
            {"implementation_status": "implemented"},
        )
