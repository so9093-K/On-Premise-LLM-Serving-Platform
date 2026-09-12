"""baseline 승격과 회귀 판정을 고정한다(ADR-0026 1·9절).

baseline은 SLO가 아니다. SLO는 제공하려는 품질이고 baseline은 특정 구성에서 나온
관찰이다. 그래서 SLO 임계값이 비어 있어도 회귀 판정은 동작한다.
"""

from __future__ import annotations

import pytest

from scripts.benchmark.baseline import (
    PromotionRefused,
    baseline_for,
    compare,
    configuration_of,
    promote,
)
from scripts.benchmark.contract import load_contract


def _document(*, run_id="r1", rate=0.1, success=1.0, lag=0.005, ttfc=0.67, samples=21,
              drift_values=None) -> dict:
    values = drift_values or [ttfc] * 8
    return {
        "run": {"id": run_id, "started_at": "2026-01-01T00:00:00Z", "duration_seconds": 100.0,
                "max_dispatch_lag_seconds": lag},
        "environment": {
            "runtime_backend": "mlx-vlm", "deployment_target": "macos-metal-static",
            "runtime_profile": "p", "model_id": "m", "model_revision": "rev",
            "git_commit": "abc1234", "platform_image": "img",
            "gpu": {"model": "Apple M5", "count": 1, "memory_kind": "unified"},
        },
        "workload": {"id": "interactive", "cache_policy": "cold", "input_tokens": 512,
                     "traffic": {"mode": "open_loop", "request_rate_per_second": rate}},
        "requests": [
            {"index": i, "succeeded": True, "client_time_to_first_chunk_seconds": v}
            for i, v in enumerate(values)
        ],
        "summary": {
            "client_request_success_ratio": {"value": success, "count": len(values)},
            "client_time_to_first_chunk_seconds": {"p50": ttfc, "p95": ttfc * 1.03, "count": samples},
            "client_output_tokens_per_second": {"value": 38.4, "count": samples},
        },
    }


def _promote(**changes):
    return promote([_document(**changes)], load_contract(), promoted_by="tester")


def test_a_run_that_dropped_requests_cannot_become_a_baseline():
    """버린 요청이 있으면 지연 분포는 살아남은 요청의 것이다.

    실측에서 1 rps는 성공률 25%였고 살아남은 요청의 첫 응답은 89.7초, 거의 전부
    큐 대기였다. 그걸 baseline으로 삼으면 이후 정상 실행이 크게 개선된 것으로 보인다.
    """
    with pytest.raises(PromotionRefused, match="success ratio"):
        _promote(success=0.9)


def test_a_harness_limited_run_cannot_become_a_baseline():
    """도구가 목표 시각을 못 지킨 실행은 서버가 아니라 도구를 잰 것이다."""
    with pytest.raises(PromotionRefused, match="dispatch lag"):
        _promote(lag=2.0)


def test_a_saturated_run_cannot_become_a_baseline():
    """용량 밖에서 잰 값을 굳히면 큐 길이가 기준이 된다."""
    with pytest.raises(PromotionRefused, match="drifted"):
        _promote(drift_values=[1.0, 1.0, 1.0, 1.0, 5.0, 6.0, 7.0, 8.0])


def test_an_undeclared_load_cannot_become_a_baseline():
    """sweep의 이분 탐색이 찾은 지점은 실행마다 달라진다.

    실제로 0.15 rps에서 승격했는데 선언된 지점은 0.1·0.2·0.5·1.0이었고, 이후 어떤
    실행도 그 baseline과 맞지 않았다.
    """
    with pytest.raises(PromotionRefused, match="declared sweep point"):
        _promote(rate=0.15)


def test_statistics_below_the_minimum_sample_count_are_not_promoted():
    """표본이 적으면 percentile은 사실상 최댓값이고, 다음 실행이 표본을 더 모으면
    그것만으로 회귀가 된다."""
    with pytest.raises(PromotionRefused, match="no comparable statistic"):
        _promote(samples=3)


def test_runs_from_different_configurations_cannot_be_merged():
    """서로 다른 구성의 관찰을 합치면 어느 구성의 값도 아니게 된다."""
    with pytest.raises(PromotionRefused, match="different configurations"):
        promote([_document(rate=0.1), _document(rate=0.2)], load_contract(), promoted_by="t")


def test_promoting_several_runs_records_the_observed_spread():
    """허용 오차가 실행 간 편차보다 작으면 정상 변동을 회귀로 잡는다."""
    baseline = promote(
        [_document(run_id="a", ttfc=0.670), _document(run_id="b", ttfc=0.680)],
        load_contract(), promoted_by="tester",
    )
    entry = baseline["statistics"]["client_time_to_first_chunk_seconds"]["p50"]
    assert entry["observed_spread_ratio"] == pytest.approx(0.01 / 0.675, rel=0.05)
    assert baseline["provenance"]["run_ids"] == ["a", "b"]


def test_a_baseline_only_matches_the_configuration_it_came_from():
    """다른 구성의 관찰과 견주면 성능이 변한 것이 아니라 구성이 다른 것을 회귀로 읽는다."""
    baseline = _promote()
    assert baseline_for(_document(), [baseline]) is baseline
    assert baseline_for(_document(rate=0.2), [baseline]) is None


@pytest.mark.parametrize("ttfc,expected", [
    (0.67, "ok"),
    (0.70, "ok"),        # 4% 변화, 허용 10% 안
    (0.80, "regressed"), # 19% 느려짐
    (0.40, "ok"),        # 빨라진 것은 회귀가 아니다
])
def test_latency_regression_uses_the_direction_the_contract_declares(ttfc, expected):
    baseline = _promote()
    result = compare(_document(ttfc=ttfc), baseline, load_contract())
    entry = next(e for e in result["comparisons"]
                 if e["metric"] == "client_time_to_first_chunk_seconds" and e["statistic"] == "p50")
    assert entry["status"] == expected


def test_throughput_regression_is_judged_in_the_other_direction():
    """지연은 커지면 회귀이고 처리량은 작아지면 회귀다."""
    baseline = _promote()
    document = _document()
    document["summary"]["client_output_tokens_per_second"]["value"] = 30.0
    result = compare(document, baseline, load_contract())
    entry = next(e for e in result["comparisons"]
                 if e["metric"] == "client_output_tokens_per_second")
    assert entry["status"] == "regressed"
    assert entry["change_ratio"] < 0


def test_the_configuration_key_is_what_determines_the_value_not_the_target_name():
    """같은 target에서 모델만 바꿔도 값이 달라진다."""
    configuration = configuration_of(_document())
    # 값을 정하는 것은 모델과 실행 설정과 부하다. 가속기는 원격 측정에서 관측되지
    # 않으므로 키가 아니며, 하드웨어 종류는 deployment_target이 정한다.
    for key in ("runtime_backend", "deployment_target", "runtime_profile",
                "model_id", "model_revision"):
        assert key in configuration
    assert configuration["request_rate_per_second"] == 0.1


def _remote_and_local(**environment) -> tuple[dict, dict]:
    """같은 배포를 GPU 장비에서 직접 재는 경우와 원격에서 재는 경우."""
    document = _document()
    document["environment"].update(environment)
    local = {**document, "environment": {
        **document["environment"],
        "gpu": {"model": "RTX 6000 Ada", "count": 1, "memory_kind": "dedicated"},
    }}
    remote = {**document, "environment": {
        **{k: v for k, v in document["environment"].items() if k != "gpu"},
        "accelerator_unavailable_reason": "client runs on macos but the target declares linux",
    }}
    return local, remote


def test_where_the_client_runs_does_not_change_the_configuration_key():
    """가속기는 런타임이 있는 곳의 것이고 client는 원격일 수 있다.

    그 차이를 구성 키에 넣으면 같은 배포를 재도 측정 위치에 따라 다른 구성이 되어
    baseline과 만나지 못한다. 하드웨어 종류는 deployment_target이 이미 정한다.
    """
    local, remote = _remote_and_local(runtime_backend="vllm-cuda",
                                      deployment_target="linux-nvidia-dynamic")
    assert configuration_of(local) == configuration_of(remote)
    assert not any("gpu" in key for key in configuration_of(local))


def test_different_targets_are_still_distinguished():
    """가속기를 키에서 빼도 target이 하드웨어 종류를 구분한다."""
    linux, _ = _remote_and_local(runtime_backend="vllm-cuda",
                                 deployment_target="linux-nvidia-dynamic")
    mac, _ = _remote_and_local(runtime_backend="mlx-vlm",
                               deployment_target="macos-metal-static")
    assert configuration_of(linux) != configuration_of(mac)


def test_a_baseline_promoted_on_the_gpu_box_matches_a_run_driven_remotely():
    """승격한 곳과 재는 곳이 달라도 같은 배포면 견줄 수 있어야 한다."""
    local, remote = _remote_and_local(runtime_backend="vllm-cuda",
                                      deployment_target="linux-nvidia-dynamic")
    baseline = promote([local], load_contract(), promoted_by="tester")
    assert baseline_for(remote, [baseline]) is baseline
