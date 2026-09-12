"""요청률 sweep이 용량을 어떻게 판정하는지 고정한다(ADR-0026 7절).

판정 규칙은 계약이 소유한다(workloads.yaml의 capacity_criterion). 여기서는 그
규칙이 실제 측정 모양에 대해 의도한 답을 내는지만 본다. 고정 입력만 쓴다.
"""

from __future__ import annotations

import pytest

from scripts.benchmark.contract import load_contract, load_yaml_mapping, WORKLOADS_PATH
from scripts.benchmark.runner import RunnerError
from scripts.benchmark.sweep import _criterion, _point_summary, drift_ratio, sweep_axis


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
        "summary": {
            "client_request_success_ratio": {"value": success},
            "client_output_tokens_per_second": {"value": 40.0},
        },
    }


CRITERION = {"max_second_half_ratio": 1.5, "require_success_ratio": 1.0,
             "min_throughput_gain_ratio": 0.1, "drift_minimum_samples": 4}


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
    point = _point_summary(_document(0.2, values, success), CRITERION, "request_rate_per_second")
    assert point["sustained"] is sustained, why


def test_the_sweep_points_come_from_the_contract():
    """훑을 지점을 코드가 정하면 계약을 읽어도 무엇을 쟀는지 알 수 없다."""
    contract = load_contract()
    declared = load_yaml_mapping(WORKLOADS_PATH)["workloads"]["interactive"]["traffic"]
    axis, points = sweep_axis(contract, "interactive")
    assert axis == "request_rate_per_second"
    assert points == sorted(float(rate) for rate in declared["request_rate_sweep"])
    # 선언한 부하가 sweep 안에 있어야 그것이 용량 안인지 밖인지 알 수 있다.
    assert float(declared["request_rate_per_second"]) in points


def test_a_workload_without_a_declared_sweep_is_refused():
    contract = load_contract()
    contract.workloads["interactive"] = {"traffic": {"mode": "open_loop"}}
    with pytest.raises(RunnerError, match="request_rate_sweep"):
        sweep_axis(contract, "interactive")


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


def test_each_traffic_mode_sweeps_the_knob_that_mode_actually_has():
    """요청률 sweep과 동시성 sweep은 다른 것을 묻는다.

    open loop은 "이 부하를 감당하는가", closed loop은 "동시에 더 돌리면 처리량이
    더 나오는가"다. 한 축으로 뭉뚱그리면 둘 중 하나는 답이 없는 질문이 된다.
    """
    contract = load_contract()
    assert sweep_axis(contract, "interactive")[0] == "request_rate_per_second"
    assert sweep_axis(contract, "batch") == (
        "concurrency",
        sorted(float(v) for v in contract.workload("batch")["traffic"]["concurrency_sweep"]),
    )


def test_closed_loop_points_are_not_judged_by_queue_drift():
    """closed loop은 client가 스스로 속도를 늦추므로 대기가 쌓이지 않는다.

    같은 규칙을 적용하면 모든 동시성이 항상 "감당"으로 나와 아무것도 구분하지 못한다.
    """
    document = {
        "workload": {"traffic": {"concurrency": 3}},
        "run": {"id": "r"},
        "requests": _samples([2.0, 4.0, 6.0, 8.0, 10.0, 12.0]),
        "summary": {
            "client_request_success_ratio": {"value": 1.0},
            "client_output_tokens_per_second": {"value": 40.0},
        },
    }
    point = _point_summary(document, CRITERION, "concurrency")
    assert point["concurrency"] == 3
    assert "time_to_first_chunk_drift" not in point
    assert point["sustained"] is True


def test_concurrency_above_the_declared_admission_limit_is_refused():
    """한도 위에서는 런타임이 아니라 Gateway 큐를 재게 된다."""
    from scripts.benchmark.runner import RunOptions, run_workload

    contract = load_contract()
    limit = contract.workload("batch")["traffic"]["admission_limit"]["max_concurrency"]
    options = RunOptions("batch", "smoke", "http://127.0.0.1:1", "", 1,
                         max_requests=1, warmup_seconds=0, concurrency=limit + 1)
    with pytest.raises(RunnerError, match="admission limit"):
        run_workload(contract, options)


def _concurrency_points(throughputs: list[float]) -> list[dict]:
    return [
        {"concurrency": i + 1, "output_tokens_per_second": value,
         "success_ratio": 1.0, "sustained": True, "requests": 6, "run_id": f"r{i}"}
        for i, value in enumerate(throughputs)
    ]


def test_concurrency_that_does_not_raise_throughput_is_not_counted():
    """성공률만 보면 어떤 동시성이든 늘 감당으로 나온다.

    실측에서 동시성 1·2·3의 처리량이 42.4·42.1·42.6 tok/s로 편차 0.6%였는데도
    세 지점이 모두 감당으로 보고됐다. 런타임이 순차 처리하면 더 나오지 않는다.
    """
    from scripts.benchmark.sweep import _mark_throughput_scaling

    points = _concurrency_points([42.4, 42.1, 42.6])
    _mark_throughput_scaling(points, load_contract().capacity_criterion)
    assert [p["sustained"] for p in points] == [True, False, False]


def test_concurrency_that_does_raise_throughput_is_counted():
    from scripts.benchmark.sweep import _mark_throughput_scaling

    points = _concurrency_points([13.6, 27.2, 40.8])
    _mark_throughput_scaling(points, load_contract().capacity_criterion)
    assert [p["sustained"] for p in points] == [True, True, True]
    assert points[1]["throughput_gain_ratio"] == pytest.approx(1.0, abs=0.01)


def test_a_dip_does_not_let_a_later_point_look_like_it_scaled():
    """직전 값이 아니라 지금까지의 최고치와 비교해야 한다.

    42 -> 30 -> 40이면 마지막이 직전 대비 +33%지만 최고치(42)는 넘지 못했다.
    직전 값만 보면 동시성을 올려 이득을 본 것처럼 보고된다.
    """
    from scripts.benchmark.sweep import _mark_throughput_scaling

    points = _concurrency_points([42.0, 30.0, 40.0])
    _mark_throughput_scaling(points, load_contract().capacity_criterion)
    assert [p["sustained"] for p in points] == [True, False, False]


def test_a_length_sweep_is_the_axis_even_though_the_mode_is_closed_loop():
    """long_context가 묻는 것은 부하가 아니라 "얼마나 긴 입력까지 처리하는가"다."""
    contract = load_contract()
    axis, points = sweep_axis(contract, "long_context")
    assert axis == "input_tokens"
    declared = contract.workload("long_context")["prompt"]["input_tokens_sweep"]
    assert points == sorted(float(v) for v in declared)


def test_a_length_point_is_not_judged_by_queue_drift_or_throughput_gain():
    """길이는 부하가 아니다. 대기가 쌓이는지도, 처리량이 더 나오는지도 묻지 않는다."""
    document = {
        "workload": {"traffic": {"concurrency": 1}, "input_tokens": 24576},
        "run": {"id": "r"},
        "requests": _samples([12.0, 12.1, 12.2, 12.0]),
        "summary": {
            "client_request_success_ratio": {"value": 1.0},
            "client_output_tokens_per_second": {"value": 9.0},
            "client_time_to_first_chunk_seconds": {"mean": 12.1, "p50": None},
        },
    }
    point = _point_summary(document, CRITERION, "input_tokens")
    assert point["input_tokens"] == 24576
    # percentile이 아니라 평균이다. 긴 입력은 최소 표본을 채우는 데 몇십 분이 걸린다.
    assert point["mean_time_to_first_chunk_seconds"] == 12.1
    assert "time_to_first_chunk_drift" not in point
    assert point["sustained"] is True
