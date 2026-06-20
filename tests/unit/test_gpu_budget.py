"""Unit tests for the pure GPU VRAM admission planner."""

from __future__ import annotations

from ai_model_serving.gpu_budget import (
    Participant,
    budget_snapshot,
    plan_activation,
)

# Secondaries share one priority tier (evict to minimise count: largest first);
# the main model is a higher, non-evictable tier.
MAIN = Participant("main", 0.76, active=True, priority=100, evictable=False)
EMB = Participant("embedding", 0.04, active=True, priority=50)
EMB_KO = Participant("embedding_ko", 0.06, active=True, priority=50)
RISK = Participant("risk_prompt", 0.065, active=True, priority=50)


def test_current_fleet_is_admissible():
    # 0.76 + 0.04 + 0.06 + 0.065 = 0.925 < 0.95
    fleet = [MAIN, EMB, EMB_KO, RISK]
    snap = budget_snapshot(fleet)
    assert snap["used"] == 0.925
    assert snap["free"] > 0


def test_reactivating_an_active_runtime_fits_in_place():
    # Replacing main with a same-cost profile: main's own residency does not count.
    fleet = [MAIN, EMB, EMB_KO, RISK]
    result = plan_activation(fleet, "main", 0.76)
    assert result.already_fits
    assert result.victims == ()


def test_second_heavy_model_requires_eviction_plan():
    # Bring up a 0.76 model "main2" while main(0.76)+secondaries(0.165) are active.
    fleet = [MAIN, EMB, EMB_KO, RISK]
    result = plan_activation(fleet, "main2", 0.76)
    # main is non-evictable, so even stopping all secondaries (0.165) cannot free
    # enough room for another 0.76 -> infeasible.
    assert not result.feasible
    assert result.victims == ()


def test_eviction_frees_room_when_possible():
    # main stopped; bring up a 0.85 model. Secondaries are evictable.
    main_stopped = Participant("main", 0.76, active=False, priority=100, evictable=False)
    fleet = [main_stopped, EMB, EMB_KO, RISK]
    result = plan_activation(fleet, "big", 0.85)
    # used_by_others = 0.165, available = 0.785; deficit = 0.065. Within one tier,
    # the largest evictable (risk_prompt 0.065) is enough -> one victim.
    assert result.needs_eviction
    assert result.victims == ("risk_prompt",)


def test_victims_minimise_count_largest_first_within_tier():
    main_stopped = Participant("main", 0.76, active=False, priority=100, evictable=False)
    fleet = [main_stopped, EMB, EMB_KO, RISK]
    # target 0.9 -> available 0.785, deficit 0.115. Largest first: risk 0.065 then
    # embedding_ko 0.06 -> 0.125 >= 0.115, two victims.
    result = plan_activation(fleet, "big", 0.9)
    assert result.feasible
    assert result.victims == ("risk_prompt", "embedding_ko")


def test_lower_priority_tier_evicted_before_higher():
    main_stopped = Participant("main", 0.76, active=False, priority=100, evictable=False)
    low = Participant("low", 0.05, active=True, priority=10)
    high = Participant("high", 0.05, active=True, priority=90)
    fleet = [main_stopped, low, high]
    # available 0.85, deficit 0.04 for a 0.89 target: the lower-priority tier goes
    # first even though both could satisfy it alone.
    result = plan_activation(fleet, "t", 0.89)
    assert result.victims == ("low",)


def test_main_never_auto_evicted_for_others():
    # Even a target that would fit only by stopping main stays infeasible, because
    # main is non-evictable.
    fleet = [MAIN, EMB]  # used_by_others for "x" = 0.80
    result = plan_activation(fleet, "x", 0.5)  # available 0.15, deficit 0.35
    # only EMB(0.04) is evictable -> cannot reach 0.35 -> infeasible, main untouched.
    assert not result.feasible
    assert "main" not in result.victims


def test_ceiling_is_respected():
    fleet = [EMB]  # 0.04 active
    # target 0.92 with ceiling 0.95: available 0.91 < 0.92 -> needs to free 0.01.
    result = plan_activation(fleet, "t", 0.92, ceiling=0.95)
    assert result.victims == ("embedding",)
    # with a higher ceiling it fits in place.
    result2 = plan_activation(fleet, "t", 0.92, ceiling=0.99)
    assert result2.already_fits
