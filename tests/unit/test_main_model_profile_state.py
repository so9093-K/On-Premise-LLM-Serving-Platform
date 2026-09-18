from __future__ import annotations

import pytest

from ai_model_serving.main_model.profile_state import (
    normalize_profile_state,
    public_compatibility_projection,
)


def test_canonical_split_state_is_preserved() -> None:
    state = normalize_profile_state(
        "candidate",
        {"status": "compatible"},
        {"status": "verified", "evidence_ref": "qualification/candidate.json"},
    )

    assert state.compatibility == {"status": "compatible"}
    assert state.qualification == {
        "status": "verified",
        "evidence_ref": "qualification/candidate.json",
    }
    assert state.legacy_status == "verified"
    assert public_compatibility_projection(state) == {
        "status": "verified",
        "technical_status": "compatible",
    }


@pytest.mark.parametrize(
    ("legacy", "compatibility", "qualification"),
    [
        ("verified", "compatible", "verified"),
        ("unverified", "compatible", "unverified"),
        ("likely", "unknown", "unverified"),
        ("unknown", "unknown", "unverified"),
        ("incompatible", "incompatible", "unverified"),
    ],
)
def test_legacy_state_is_read_compatibly(
    legacy: str,
    compatibility: str,
    qualification: str,
) -> None:
    state = normalize_profile_state("legacy", {"status": legacy}, None)

    assert state.compatibility["status"] == compatibility
    assert state.qualification["status"] == qualification
    assert state.legacy_status == legacy
    assert public_compatibility_projection(state)["status"] == legacy


def test_existing_qualification_metadata_survives_legacy_migration() -> None:
    state = normalize_profile_state(
        "mlx",
        {"status": "verified"},
        {
            "normal": {"input_tokens": 24576},
            "extended": {"images_max": 8},
        },
    )

    assert state.qualification["status"] == "verified"
    assert state.qualification["normal"]["input_tokens"] == 24576
    assert state.qualification["extended"]["images_max"] == 8


def test_canonical_compatibility_requires_qualification_status() -> None:
    with pytest.raises(ValueError, match="requires qualification.status"):
        normalize_profile_state("candidate", {"status": "compatible"}, None)


def test_legacy_status_cannot_mix_with_new_qualification_status() -> None:
    with pytest.raises(ValueError, match="must not combine legacy"):
        normalize_profile_state(
            "candidate",
            {"status": "verified"},
            {"status": "verified"},
        )
