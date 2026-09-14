from __future__ import annotations

import pytest

from ai_model_serving.gpu_budget import Participant
from ai_model_serving.runtime_transition import plan_runtime_transition
from ai_model_serving.runtime_transition_contract import parse_runtime_transition_request
from ai_model_serving.errors import ServiceError


def _participant(
    key: str,
    fraction: float,
    *,
    active: bool,
    priority: int = 0,
    evictable: bool = True,
    criticality: str | None = None,
) -> Participant:
    return Participant(
        key=key,
        vram_fraction=fraction,
        active=active,
        priority=priority,
        evictable=evictable,
        criticality=criticality,
    )


def test_activation_plan_exposes_victims_without_mutating_state() -> None:
    participants = [
        _participant("embed", 0.20, active=True, priority=10, criticality="retrieval_support_path"),
        _participant("risk", 0.10, active=True, priority=20, criticality="risk_signal_path"),
        _participant("embed-ko", 0.20, active=False, priority=10, criticality="retrieval_support_path"),
        _participant("main", 0.60, active=True, priority=100, evictable=False, criticality="primary_user_path"),
    ]

    plan = plan_runtime_transition(
        participants,
        "embed-ko",
        "active",
        force=False,
        ceiling=0.93,
    )

    assert plan.admissible is False
    assert plan.requires_force is True
    assert plan.start == ("embed-ko",)
    assert plan.stop == ("embed",)
    assert plan.budget_before == {"ceiling": 0.93, "used": 0.9, "free": 0.03}
    assert plan.budget_after == {"ceiling": 0.93, "used": 0.9, "free": 0.03}
    assert plan.impact == (
        {"key": "embed", "action": "stop", "criticality": "retrieval_support_path"},
    )
    assert participants[0].active is True


def test_force_is_part_of_plan_digest_and_makes_eviction_admissible() -> None:
    participants = [
        _participant("embed", 0.20, active=True, priority=10),
        _participant("target", 0.20, active=False, priority=10),
        _participant("main", 0.70, active=True, priority=100, evictable=False),
    ]
    preview = plan_runtime_transition(participants, "target", "active", force=False, ceiling=0.93)
    forced = plan_runtime_transition(participants, "target", "active", force=True, ceiling=0.93)

    assert preview.stop == forced.stop == ("embed",)
    assert preview.admissible is False
    assert forced.admissible is True
    assert preview.digest != forced.digest
    assert forced.digest == plan_runtime_transition(
        participants, "target", "active", force=True, ceiling=0.93
    ).digest


def test_transition_protects_current_target_while_starting_missing_prerequisite() -> None:
    participants = [
        _participant("target", 0.20, active=True, priority=0, evictable=True),
        _participant("prereq", 0.10, active=False, priority=0, evictable=True),
        _participant("main", 0.70, active=True, priority=100, evictable=False),
    ]

    plan = plan_runtime_transition(
        participants,
        "target",
        "active",
        force=True,
        prerequisites=["prereq"],
        ceiling=0.93,
    )

    assert plan.start == ("prereq",)
    assert plan.stop == ()
    assert plan.admissible is False
    assert plan.reason is not None


def test_stop_and_already_active_plans_are_explicit_noop_or_budget_change() -> None:
    participants = [
        _participant("target", 0.20, active=True, criticality="retrieval_support_path"),
        _participant("main", 0.60, active=True, priority=100, evictable=False),
    ]
    active = plan_runtime_transition(participants, "target", "active", ceiling=0.93)
    stopped = plan_runtime_transition(participants, "target", "stopped", ceiling=0.93)
    stopped_with_irrelevant_force = plan_runtime_transition(
        participants, "target", "stopped", force=True, ceiling=0.93
    )

    assert active.no_op is True
    assert active.start == ()
    assert stopped.no_op is False
    assert stopped.stop == ("target",)
    assert stopped.budget_after["used"] == pytest.approx(0.6)
    # force는 activation eviction에만 의미가 있으므로 stop plan에서는 canonical false다.
    # 그래야 기존 caller가 force=true를 보내도 review/apply digest가 drift하지 않는다.
    assert stopped_with_irrelevant_force.force is False
    assert stopped_with_irrelevant_force.digest == stopped.digest


def test_runtime_request_parser_is_strict_and_digest_is_optional_for_legacy_apply() -> None:
    assert parse_runtime_transition_request(
        {"desired_state": "active", "force": False}, allow_plan_digest=True
    ) == ("active", False, None)

    with pytest.raises(ServiceError) as force_error:
        parse_runtime_transition_request(
            {"desired_state": "active", "force": "false"}, allow_plan_digest=True
        )
    assert force_error.value.code == "VALIDATION_ERROR"
    assert force_error.value.param == "force"

    with pytest.raises(ServiceError) as field_error:
        parse_runtime_transition_request(
            {"desired_state": "active", "unexpected": True}, allow_plan_digest=False
        )
    assert field_error.value.param == "unexpected"

    with pytest.raises(ServiceError) as digest_error:
        parse_runtime_transition_request(
            {"desired_state": "active", "plan_digest": "not-a-digest"},
            allow_plan_digest=True,
        )
    assert digest_error.value.param == "plan_digest"
