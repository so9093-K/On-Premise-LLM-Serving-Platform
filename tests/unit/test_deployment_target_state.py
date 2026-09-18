from __future__ import annotations

import pytest

from ai_model_serving.deployment_target_state import normalize_deployment_target_state


@pytest.mark.parametrize(
    ("legacy", "implementation", "qualification"),
    [
        ("verified", "implemented", "verified"),
        ("implemented", "implemented", "unverified"),
        ("planned", "planned", "unverified"),
        ("unvalidated", "planned", "unverified"),
    ],
)
def test_legacy_validation_state_is_read_compatibly(
    legacy: str,
    implementation: str,
    qualification: str,
) -> None:
    state = normalize_deployment_target_state(
        "legacy-target",
        {"validation_status": legacy},
    )

    assert state.implementation_status == implementation
    assert state.qualification_status == qualification
    assert state.legacy_validation_status == legacy


@pytest.mark.parametrize(
    ("implementation", "qualification", "legacy"),
    [
        ("implemented", "verified", "verified"),
        ("implemented", "unverified", "implemented"),
        ("planned", "unverified", "planned"),
    ],
)
def test_canonical_state_projects_legacy_status(
    implementation: str,
    qualification: str,
    legacy: str,
) -> None:
    state = normalize_deployment_target_state(
        "canonical-target",
        {
            "implementation_status": implementation,
            "qualification_status": qualification,
        },
    )

    assert state.implementation_status == implementation
    assert state.qualification_status == qualification
    assert state.legacy_validation_status == legacy


def test_canonical_and_legacy_state_cannot_be_mixed() -> None:
    with pytest.raises(ValueError, match="must not combine canonical state"):
        normalize_deployment_target_state(
            "mixed-target",
            {
                "implementation_status": "implemented",
                "qualification_status": "verified",
                "validation_status": "verified",
            },
        )


def test_planned_target_cannot_be_qualification_verified() -> None:
    with pytest.raises(ValueError, match="cannot be qualification verified"):
        normalize_deployment_target_state(
            "impossible-target",
            {
                "implementation_status": "planned",
                "qualification_status": "verified",
            },
        )


def test_partial_canonical_state_fails_closed() -> None:
    with pytest.raises(ValueError, match="invalid qualification_status"):
        normalize_deployment_target_state(
            "partial-target",
            {"implementation_status": "implemented"},
        )
