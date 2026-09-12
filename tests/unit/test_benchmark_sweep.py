"""요청률 sweep이 용량을 어떻게 판정하는지 고정한다(ADR-0026 7절).

판정 규칙은 계약이 소유한다(workloads.yaml의 capacity_criterion). 여기서는 그
규칙이 실제 측정 모양에 대해 의도한 답을 내는지만 본다. 고정 입력만 쓴다.
"""

from __future__ import annotations

import pytest

from scripts.benchmark.contract import load_contract, load_yaml_mapping, WORKLOADS_PATH
from scripts.benchmark.runner import RunnerError
from scripts.benchmark.sweep import _criterion, _point_summary, drift_ratio, rate_points


def _samples(values: list[float]) -> list[dict]:
    return [
        {"index": i, "succeeded": True, "client_time_to_first_chunk_seconds": v}
        for i, v in enumerate(values)
    ]


def test_a_stable_run_shows_no_drift():
    """안정된 계에서는 요청 순서와 첫 응답 시간이 무관하다.

    실측: 0.10 rps에서 1.03배.
    """
    assert drift_ratio(_samples([0.67, 0.66, 0.68, 0.67, 0.66, 0.68])) == pytest.approx(1.0, abs=0.05)


def test_a_saturated_run_shows_the_queue_growing():
    """포화되면 뒤쪽 요청일수록 앞의 요청을 기다린다.

    실측: 0.20 rps에서 2.88배.
    """
    assert drift_ratio(_samples([2.0, 4.0, 6.0, 8.0, 10.0, 12.0])) > 1.5


def test_drift_is_not_reported_from_too_few_samples():
    """두 건으로 앞뒤를 나누면 한 건씩이라 중앙값이 그냥 그 값이다."""
    assert drift_ratio(_samples([1.0, 5.0])) is None


def test_one_slow_request_does_not_by_itself_mark_the_rate_unsustainable():
    """중앙값을 쓰는 이유다. 평균이나 최댓값은 한 건의 튐에 흔들린다."""
    ratio = drift_ratio(_samples([0.67, 0.66, 0.68, 0.67, 9.9, 0.68]))
    assert ratio is not None and ratio < 1.5


def _document(rate: float, values: list[float], success: float) -> dict:
    return {
        "workload": {"traffic": {"request_rate_per_second": rate}},
        "run": {"id": "r"},
        "requests": _samples(values),
        "summary": {"client_request_success_ratio": {"value": success}},
    }


CRITERION = {"max_second_half_ratio": 1.5, "require_success_ratio": 1.0}


@pytest.mark.parametrize(
    "values,success,sustained,why",
    [
        ([0.67] * 6, 1.0, True, "지연이 평평하고 전부 성공"),
        ([2.0, 4.0, 6.0, 8.0, 10.0, 12.0], 1.0, False, "대기가 쌓임"),
        # 요청을 하나라도 버리면 그 부하는 이미 감당 범위 밖이다. 남은 요청만
        # 보면 지연이 멀쩡해 보이는 것이 함정이다.
        ([0.67] * 6, 0.8, False, "지연은 평평하지만 20%를 버림"),
    ],
)
def test_a_rate_counts_as_sustained_only_when_both_signals_hold(values, success, sustained, why):
    point = _point_summary(_document(0.2, values, success), CRITERION)
    assert point["sustained"] is sustained, why


def test_the_sweep_points_come_from_the_contract():
    """훑을 지점을 코드가 정하면 계약을 읽어도 무엇을 쟀는지 알 수 없다."""
    contract = load_contract()
    declared = load_yaml_mapping(WORKLOADS_PATH)["workloads"]["interactive"]["traffic"]
    assert rate_points(contract, "interactive") == sorted(
        float(rate) for rate in declared["request_rate_sweep"]
    )
    # 선언한 부하가 sweep 안에 있어야 그것이 용량 안인지 밖인지 알 수 있다.
    assert float(declared["request_rate_per_second"]) in rate_points(contract, "interactive")


def test_a_workload_without_a_declared_sweep_is_refused():
    contract = load_contract()
    contract.workloads["interactive"] = {"traffic": {"mode": "open_loop"}}
    with pytest.raises(RunnerError, match="request_rate_sweep"):
        rate_points(contract, "interactive")


def test_the_capacity_criterion_comes_from_the_contract_object():
    """sweep이 workloads.yaml을 따로 열면 계약 객체와 파일이 갈라진다."""
    contract = load_contract()
    assert _criterion(contract) == contract.capacity_criterion
    assert contract.capacity_criterion["signal"] == "time_to_first_chunk_drift"


def test_an_unimplemented_capacity_signal_is_refused():
    """계약이 다른 신호를 선언했는데 이 구현으로 판정하면 다른 답이 나온다."""
    from dataclasses import replace as dataclass_replace

    contract = load_contract()
    drifted = dataclass_replace(contract, capacity_criterion={"signal": "queue_depth"})
    with pytest.raises(RunnerError, match="time_to_first_chunk_drift"):
        _criterion(drifted)
