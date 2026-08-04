"""순수 GPU VRAM admission planner에 대한 단위 테스트."""

from __future__ import annotations

from ai_model_serving.gpu_budget import (
    Participant,
    budget_snapshot,
    plan_activation,
)

# secondary들은 하나의 priority tier를 공유한다(evict 시 개수 최소화: 가장 큰 것부터);
# main model은 더 높은, evict 불가능한 tier다.
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
    # main을 동일 비용 프로필로 교체: main 자신의 점유량은 계산에 포함되지 않는다.
    fleet = [MAIN, EMB, EMB_KO, RISK]
    result = plan_activation(fleet, "main", 0.76)
    assert result.already_fits
    assert result.victims == ()


def test_second_heavy_model_requires_eviction_plan():
    # main(0.76)+secondaries(0.165)가 활성 상태인 채로 0.76짜리 모델 "main2"를 올린다.
    fleet = [MAIN, EMB, EMB_KO, RISK]
    result = plan_activation(fleet, "main2", 0.76)
    # main은 evict 불가능하므로, secondary를 전부 멈춰도(0.165) 또 다른 0.76을
    # 위한 공간을 확보할 수 없다 -> 불가능.
    assert not result.feasible
    assert result.victims == ()


def test_eviction_frees_room_when_possible():
    # main은 중지됨; 0.85짜리 모델을 올린다. secondary들은 evict 가능.
    main_stopped = Participant("main", 0.76, active=False, priority=100, evictable=False)
    fleet = [main_stopped, EMB, EMB_KO, RISK]
    result = plan_activation(fleet, "big", 0.85)
    # used_by_others = 0.165, available = 0.785; deficit = 0.065. 하나의 tier
    # 안에서, 가장 큰 evict 대상(risk_prompt 0.065)이면 충분하다 -> 희생자 1개.
    assert result.needs_eviction
    assert result.victims == ("risk_prompt",)


def test_victims_minimise_count_largest_first_within_tier():
    main_stopped = Participant("main", 0.76, active=False, priority=100, evictable=False)
    fleet = [main_stopped, EMB, EMB_KO, RISK]
    # target 0.9 -> available 0.785, deficit 0.115. 큰 것부터: risk 0.065 다음
    # embedding_ko 0.06 -> 0.125 >= 0.115, 희생자 2개.
    result = plan_activation(fleet, "big", 0.9)
    assert result.feasible
    assert result.victims == ("risk_prompt", "embedding_ko")


def test_lower_priority_tier_evicted_before_higher():
    main_stopped = Participant("main", 0.76, active=False, priority=100, evictable=False)
    low = Participant("low", 0.05, active=True, priority=10)
    high = Participant("high", 0.05, active=True, priority=90)
    fleet = [main_stopped, low, high]
    # target 0.89에 대해 available 0.85, deficit 0.04: 둘 다 단독으로 충분해도
    # 더 낮은 priority tier가 먼저 나간다.
    result = plan_activation(fleet, "t", 0.89)
    assert result.victims == ("low",)


def test_criticality_policy_evicts_retrieval_before_risk():
    # sidecar의 criticality->priority 매핑을 그대로 반영한다: retrieval_support_path
    # (50)가 risk_signal_path(60)보다 먼저 evict된다; main은 evict 불가능.
    main = Participant("main", 0.76, active=False, priority=100, evictable=False)
    emb = Participant("embedding", 0.04, active=True, priority=50)
    emb_ko = Participant("embedding_ko", 0.06, active=True, priority=50)
    risk = Participant("risk_prompt", 0.065, active=True, priority=60)
    fleet = [main, emb, emb_ko, risk]
    # main은 중지됨; target 0.9를 올린다. available 0.765, deficit 0.135.
    # retrieval tier가 먼저(0.06+0.04=0.10 < 0.135), 그다음 risk(0.165 >= 0.135).
    result = plan_activation(fleet, "main", 0.9)
    assert result.victims == ("embedding_ko", "embedding", "risk_prompt")
    # retrieval만으론 부족했기 때문에만 risk가 선택된다; 그래서 마지막이다.
    assert result.victims[-1] == "risk_prompt"


def test_main_never_auto_evicted_for_others():
    # main을 멈춰야만 들어맞는 target이라도, main은 evict 불가능하므로
    # 여전히 불가능한 채로 남는다.
    fleet = [MAIN, EMB]  # "x"에 대한 used_by_others = 0.80
    result = plan_activation(fleet, "x", 0.5)  # available 0.15, deficit 0.35
    # EMB(0.04)만 evict 가능 -> 0.35에 도달 불가 -> 불가능, main은 그대로.
    assert not result.feasible
    assert "main" not in result.victims


def test_ceiling_is_respected():
    fleet = [EMB]  # 0.04 active
    # ceiling 0.95에서 target 0.92: available 0.91 < 0.92 -> 0.01을 확보해야 한다.
    result = plan_activation(fleet, "t", 0.92, ceiling=0.95)
    assert result.victims == ("embedding",)
    # ceiling이 더 높으면 그대로 들어맞는다.
    result2 = plan_activation(fleet, "t", 0.92, ceiling=0.99)
    assert result2.already_fits
