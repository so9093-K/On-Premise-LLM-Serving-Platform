"""집계와 SLO 판정을 고정한다(ADR-0026 9·10절).

전부 고정 입력으로 검증한다. 벽시계를 재면 부하가 걸린 머신에서 측정이 아니라
그 머신의 여유를 재게 된다 -- runner 테스트에서 실제로 그렇게 깨졌다.
"""

from __future__ import annotations

import pytest

from scripts.benchmark.contract import PerformanceContract, load_contract
from scripts.benchmark.evaluate import EvaluationError, evaluate, percentile, summarize


def _document(requests: list[dict], *, window: float = 10.0, workload: str = "interactive") -> dict:
    return {
        "schema_version": 1,
        "contract_version": 1,
        "run": {"id": "r", "mode": "smoke", "started_at": "2026-01-01T00:00:00Z",
                "duration_seconds": window + 1, "dispatch_window_seconds": window, "seed": 1},
        "environment": {},
        "workload": {"id": workload, "cache_policy": "cold", "traffic": {"mode": "open_loop"}},
        "requests": requests,
        "summary": {},
    }


def _request(index: int, *, ok: bool = True, ttfc: float = 1.0, duration: float = 5.0,
             out: int = 100, inp: int = 500) -> dict:
    if not ok:
        return {"index": index, "succeeded": False, "status_code": 503,
                "error_code": "QUEUE_TIMEOUT", "client_operation_duration_seconds": duration}
    return {
        "index": index, "succeeded": True, "status_code": 200,
        "client_time_to_first_chunk_seconds": ttfc,
        "client_operation_duration_seconds": duration,
        "client_time_per_output_token_seconds": (duration - ttfc) / (out - 1),
        "client_input_tokens": inp, "client_output_tokens": out,
    }


def _contract(objectives: dict, *, enforcement: str = "observe") -> PerformanceContract:
    """SLO objective만 바꾼 실제 계약.

    지표 정의와 통계 정책은 저장소의 것을 그대로 쓴다. 가짜로 채우면 테스트가
    평가기의 실제 경로를 타지 않는다.
    """
    real = load_contract()
    return PerformanceContract(
        version=real.version,
        metrics=real.metrics,
        workloads={"interactive": {}},
        slo_classes={"interactive": {"workload": "interactive", "enforcement": enforcement,
                                     "objectives": objectives}},
        statistics=real.statistics,
    )


@pytest.mark.parametrize("name,expected", [("p50", 5), ("p95", 10), ("p99", 10)])
def test_percentile_is_nearest_rank_not_interpolated(name, expected):
    """보간하면 표본에 없던 값이 판정 기준이 된다."""
    assert percentile([float(v) for v in range(1, 11)], name) == expected


def test_percentile_below_the_minimum_sample_count_is_not_reported():
    """20개로 p99를 내면 사실상 최댓값이고, 판정이 표본 수에 달린다."""
    document = _document([_request(i, ttfc=float(i)) for i in range(30)])
    summary = summarize(document, _contract({}))
    entry = summary["client_time_to_first_chunk_seconds"]
    assert entry["count"] == 30
    assert entry["p50"] is not None and entry["p95"] is not None
    assert entry["p99"] is None, "표본 30개로는 p99를 구분할 수 없다"


def test_rejected_requests_are_excluded_from_latency_but_counted_in_success_ratio():
    """거절까지 걸린 시간은 응답 지연이 아니다. 섞으면 많이 버릴수록 빨라 보인다."""
    requests = [_request(0, ttfc=1.0), _request(1, ttfc=1.0), _request(2, ok=False, duration=0.01)]
    summary = summarize(_document(requests), _contract({}))
    assert summary["client_time_to_first_chunk_seconds"]["count"] == 2
    assert summary["client_operation_duration_seconds"]["min"] == 5.0
    assert summary["client_request_success_ratio"] == {"value": pytest.approx(2 / 3), "count": 3}


def test_throughput_uses_the_dispatch_window_as_denominator():
    document = _document([_request(i, out=100, inp=500) for i in range(4)], window=10.0)
    summary = summarize(document, _contract({}))
    assert summary["client_output_tokens_per_second"]["value"] == pytest.approx(40.0)
    assert summary["client_total_tokens_per_second"]["value"] == pytest.approx(240.0)


def test_chunk_gap_metric_reports_only_the_statistics_the_contract_allows():
    """전송 계층이 chunk를 묶어 보내면 percentile은 서버가 아니라 묶임을 잰다."""
    request = _request(0)
    request["client_time_per_output_chunk_seconds"] = [0.001, 0.001, 0.098]
    summary = summarize(_document([request]), _contract({}))
    entry = summary["client_time_per_output_chunk_seconds"]
    # 계약이 mean과 sum만 해석 가능하다고 선언했으므로 percentile 키 자체가 없다.
    # None으로 채우면 "계산했는데 표본이 모자랐다"와 구분되지 않는다.
    assert not {"p50", "p95", "p99", "min", "max"} & set(entry), sorted(entry)
    assert entry["mean"] == pytest.approx(0.1 / 3)
    assert entry["value"] == pytest.approx(0.1)


def test_higher_is_better_metrics_are_not_judged_as_if_lower_were_better():
    """성공률 임계값이 뒤집히면 100% 성공이 실패로 판정된다."""
    document = _document([_request(i) for i in range(4)])
    contract = _contract({"client_request_success_ratio": {"aggregate": "min", "threshold": 0.99,
                                                           "source": "product_requirement"}})
    verdict = evaluate(document, contract)["verdict"]
    entry = next(o for o in verdict["objectives"] if o["metric"] == "client_request_success_ratio")
    assert entry["observed"] == 1.0
    assert entry["status"] == "pass"


def test_a_latency_objective_fails_when_the_observed_value_exceeds_the_threshold():
    document = _document([_request(i, ttfc=3.0) for i in range(30)])
    contract = _contract({"client_time_to_first_chunk_seconds": {"percentiles": ["p50"],
                                                                 "threshold": 2.0,
                                                                 "source": "product_requirement"}})
    verdict = evaluate(document, contract)["verdict"]
    assert verdict["status"] == "fail"
    assert verdict["objectives"][0]["observed"] == 3.0


def test_goodput_is_not_reported_without_thresholds_and_is_not_replaced_by_success_ratio():
    """계약의 goodput은 SLO를 만족한 요청만 센다. 임계값이 없으면 정의되지 않는다."""
    summary = summarize(_document([_request(i) for i in range(4)]), _contract({}))
    assert summary["client_goodput_requests_per_second"]["value"] is None
    assert summary["client_request_success_ratio"]["value"] == 1.0


def test_goodput_counts_only_requests_inside_the_latency_threshold():
    requests = [_request(0, ttfc=1.0), _request(1, ttfc=1.0), _request(2, ttfc=9.0)]
    contract = _contract({"client_time_to_first_chunk_seconds": {"percentiles": ["p95"],
                                                                 "threshold": 2.0,
                                                                 "source": "product_requirement"}})
    summary = summarize(_document(requests, window=10.0), contract)
    # 3건 모두 성공했지만 1건은 임계값을 넘었다. 처리량과 goodput이 달라야 한다.
    assert summary["client_request_success_ratio"]["value"] == 1.0
    assert summary["client_goodput_requests_per_second"]["value"] == pytest.approx(0.2)


@pytest.mark.parametrize(
    "objectives,metric,expected",
    [
        ({"client_request_success_ratio": {"aggregate": "min"}},
         "client_request_success_ratio", "no_threshold"),
        ({"client_time_to_first_chunk_seconds": {"percentiles": ["p99"]}},
         "client_time_to_first_chunk_seconds", "insufficient_samples"),
        # goodput은 계산 자체가 임계값을 요구한다. "측정값 없음"으로 보고하면
        # 측정에 실패한 줄 알고 엉뚱한 곳을 보게 된다.
        ({"client_goodput_requests_per_second": {"aggregate": "min"}},
         "client_goodput_requests_per_second", "no_threshold"),
    ],
)
def test_not_evaluated_says_which_thing_is_missing(objectives, metric, expected):
    """표본을 더 모으는 것과 임계값을 정하는 것은 다른 작업이다."""
    verdict = evaluate(_document([_request(i) for i in range(4)]), _contract(objectives))["verdict"]
    entry = next(o for o in verdict["objectives"] if o["metric"] == metric)
    assert entry["status"] == "not_evaluated"
    assert entry["not_evaluated_reason"] == expected


def test_an_unimplemented_percentile_method_is_refused():
    """계약이 다른 방법을 선언했는데 이 구현으로 계산하면 판정이 달라진다."""
    base = _contract({})
    drifted = PerformanceContract(
        version=base.version, metrics=base.metrics, workloads=base.workloads,
        slo_classes=base.slo_classes, statistics={"percentile_method": "linear"},
    )
    with pytest.raises(EvaluationError, match="nearest_rank"):
        summarize(_document([_request(0)]), drifted)


def test_a_contract_rename_is_refused_instead_of_silently_producing_the_old_name():
    """창 단위 지표는 계산이 제각각이라 이름을 코드가 안다.

    계약이 이름을 바꾸면 평가기는 예전 이름으로 값을 내고, 판정은 그 값을 찾지
    못해 "측정값 없음"이 된다. 실제로는 측정된 값이다. 주입해서 확인한 동작이다.
    """
    base = _contract({})
    renamed = dict(base.metrics)
    renamed["client_success_ratio"] = renamed.pop("client_request_success_ratio")
    drifted = PerformanceContract(
        version=base.version, metrics=renamed, workloads=base.workloads,
        slo_classes=base.slo_classes, statistics=base.statistics,
    )
    with pytest.raises(EvaluationError, match="client_success_ratio"):
        summarize(_document([_request(0)]), drifted)


def test_an_objective_the_summary_never_produced_is_refused():
    """계약과 코드가 어긋난 것을 '측정하지 못함'과 같은 결과로 보고하면 안 된다."""
    document = _document([_request(0)])
    contract = _contract({"client_request_success_ratio": {"aggregate": "min"}})
    summary = summarize(document, contract)
    summary.pop("client_request_success_ratio")
    with pytest.raises(EvaluationError, match="no summary entry"):
        from scripts.benchmark.evaluate import judge

        judge(document, contract, summary)


MEASURED_ON = {
    "runtime_backend": "mlx-vlm",
    "deployment_target": "macos-metal-static",
    "model_id": "gemma-26b-mlx",
    "gpu_model": "Apple M5",
}


def _run_on(tpot: float, *, samples: int = 12, **changes: str) -> dict:
    # 최소 표본을 못 채우면 임계값이 아니라 표본 수 때문에 not_evaluated가 나와
    # 이 테스트가 무엇도 확인하지 못한다. p50은 10개, p95는 20개를 요구한다.
    document = _document([_request(i) for i in range(samples)])
    environment = dict(MEASURED_ON) | changes
    document["environment"] = {
        "runtime_backend": environment["runtime_backend"],
        "deployment_target": environment["deployment_target"],
        "model_id": environment["model_id"],
        "gpu": {"model": environment["gpu_model"], "count": 1, "memory_kind": "unified"},
    }
    for request in document["requests"]:
        request["client_time_per_output_token_seconds"] = tpot
    return document


def _guard_contract():
    return _contract({
        "client_time_per_output_token_seconds": {
            "percentiles": ["p50"],
            "source": "regression_guard",
            "thresholds_by_configuration": [
                {"match": dict(MEASURED_ON), "threshold": 0.030, "measured": "테스트 고정값"}
            ],
        }
    })


@pytest.mark.parametrize(
    "tpot,changes,expected",
    [
        (0.021, {}, "pass"),
        (0.042, {}, "fail"),
        # 같은 장비라도 모델이 바뀌면 그 숫자로 판정할 근거가 없다. target 이름을
        # 키로 쓰면 이 경우가 조용히 예전 선을 그대로 적용받는다.
        (0.021, {"model_id": "qwen-30b-mlx"}, "not_evaluated"),
        (0.021, {"gpu_model": "Apple M3 Ultra"}, "not_evaluated"),
        (0.021, {"runtime_backend": "vllm-cuda"}, "not_evaluated"),
    ],
)
def test_measured_guards_apply_only_to_the_configuration_they_were_measured_on(tpot, changes, expected):
    """측정으로 그은 선은 모델과 가속기와 런타임 backend가 정한다.

    다른 구성의 숫자를 빌려 쓰면 없는 근거로 합격·불합격을 내게 된다.
    """
    verdict = evaluate(_run_on(tpot, **changes), _guard_contract())["verdict"]
    assert verdict["objectives"][0]["status"] == expected


def test_a_typo_in_the_match_does_not_widen_the_guard_to_every_configuration():
    """모르는 항목을 건너뛰면 오타 하나로 조건이 사라진다."""
    contract = _contract({
        "client_time_per_output_token_seconds": {
            "percentiles": ["p50"],
            "source": "regression_guard",
            "thresholds_by_configuration": [
                {"match": {"gpu_modell": "Apple M5"}, "threshold": 0.030, "measured": "오타"}
            ],
        }
    })
    verdict = evaluate(_run_on(0.042), contract)["verdict"]
    assert verdict["objectives"][0]["status"] == "not_evaluated"


def test_a_direct_threshold_applies_to_every_configuration():
    """선언된 timeout처럼 모든 구성에 같은 한도가 걸리는 경우는 하나로 적는다."""
    contract = _contract({
        "client_operation_duration_seconds": {
            "percentiles": ["p50"], "threshold": 125, "source": "declared_limit",
        }
    })
    verdict = evaluate(_run_on(0.021, runtime_backend="vllm-cuda"), contract)["verdict"]
    assert verdict["objectives"][0]["threshold"] == 125
    assert verdict["objectives"][0]["status"] == "pass"


def test_apple_silicon_records_its_accelerator_rather_than_claiming_none():
    """맥에도 GPU가 있다. 모델은 Metal GPU에서 돈다.

    비워 두면 32GB M5와 128GB M3 Ultra가 같은 지문을 내고, 6개월 뒤 숫자를 해석할
    수 없다. 지문을 required로 잡은 이유가 그것이다.
    """
    import platform

    from scripts.benchmark import fingerprint

    gpu = fingerprint._apple_silicon_gpu()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        assert gpu is None
        return
    assert gpu is not None and gpu["count"] == 1
    # 통합 메모리는 NVIDIA의 전용 VRAM과 의미가 다르다. 같은 숫자라도 OS와 다른
    # 프로세스가 같은 풀을 쓴다.
    assert gpu["memory_kind"] == "unified"
    assert gpu["memory_bytes"] > 0
    assert gpu["model"] != platform.machine(), "칩 이름이 없으면 장비를 구분할 수 없다"


def _run_at_rate(rate: float, ttfc: float) -> dict:
    document = _run_on(0.021, samples=21)
    document["workload"]["traffic"]["request_rate_per_second"] = rate
    for request in document["requests"]:
        request["client_time_to_first_chunk_seconds"] = ttfc
    return document


def _rate_pinned_contract():
    return _contract({
        "client_time_to_first_chunk_seconds": {
            "percentiles": ["p50", "p95"],
            "source": "regression_guard",
            "thresholds_by_configuration": [
                {
                    "match": dict(MEASURED_ON) | {"request_rate_per_second": 0.15},
                    "statistic": "p95",
                    "threshold": 1.0,
                    "measured": "테스트 고정값",
                }
            ],
        }
    })


@pytest.mark.parametrize(
    "rate,ttfc,statistic,expected",
    [
        (0.15, 0.68, "p95", "pass"),
        (0.15, 2.00, "p95", "fail"),
        # 용량 밖에서 잰 값은 모델이 아니라 대기열 길이다. 실측에서 1 rps의 첫 응답은
        # 89.7초였고, 같은 구성에서 0.15 rps는 0.68초였다.
        (1.00, 89.7, "p95", "not_evaluated"),
        # 같은 부하라도 p95에서 잰 선으로 p50을 판정하지 않는다.
        (0.15, 0.68, "p50", "not_evaluated"),
    ],
)
def test_a_load_dependent_guard_applies_only_at_the_load_it_was_measured_at(
    rate, ttfc, statistic, expected
):
    verdict = evaluate(_run_at_rate(rate, ttfc), _rate_pinned_contract())["verdict"]
    entry = next(
        o for o in verdict["objectives"]
        if o["metric"] == "client_time_to_first_chunk_seconds" and o["statistic"] == statistic
    )
    assert entry["status"] == expected
