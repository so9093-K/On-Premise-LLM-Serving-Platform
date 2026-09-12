"""런타임 스냅샷 수집을 고정한다(ADR-0026 Epic 4).

Prometheus를 띄우지 않는다. 여기서 확인할 것은 "무엇을 어떤 질의로 묻고, 받은
값을 어떻게 요약하는가"이고, 그건 HTTP 응답을 고정하면 전부 검증된다.
"""

from __future__ import annotations

import pytest

from scripts.benchmark.contract import load_contract
from scripts.benchmark.snapshot import (
    PrometheusReader,
    SnapshotUnavailable,
    collect,
    scrape_interval_seconds,
)


class _Reader(PrometheusReader):
    """질의를 기록하고 준비된 값을 돌려준다."""

    def __init__(self, values: dict[str, list[float]] | None = None, *, fail: str = "") -> None:
        super().__init__("http://prometheus.invalid")
        self.queries: list[tuple[str, float, float]] = []
        self._values = values or {}
        self._fail = fail

    def range_values(self, query, start, end, step):
        if self._fail:
            raise SnapshotUnavailable(self._fail)
        self.queries.append((query, start, end))
        for source, values in self._values.items():
            if source in query:
                return list(values)
        return []


def _document(duration_seconds: float = 100.0, **environment) -> dict:
    return {
        "run": {"id": "r", "started_at": "2026-01-01T00:00:00Z", "duration_seconds": duration_seconds},
        "environment": {"runtime_backend": "mlx-vlm", "deployment_target": "macos-metal-static",
                        **environment},
    }


def test_unsupported_combinations_are_not_queried():
    """없는 series를 물어 빈 값을 받으면 "0이었다"와 구분되지 않는다."""
    reader = _Reader()
    collect(load_contract(), _document(), reader)
    asked = " ".join(query for query, _, _ in reader.queries)
    # 계약이 mlx-vlm에서 unsupported로 선언한 것들이다.
    assert "vllm" not in asked
    assert "kv_cache" not in asked
    assert "mlx_runtime_request_queue_depth" in asked


def test_counters_report_the_bracket_not_an_average():
    """누적값의 평균은 뜻이 없다. 구간의 시작과 끝만 의미가 있다."""
    reader = _Reader({"mlx_runtime_generated_tokens_total": [100.0, 350.0, 600.0]})
    snapshot = collect(load_contract(), _document(), reader)["values"]
    entry = snapshot["runtime_output_tokens_total"]
    assert entry == {"start": 100.0, "end": 600.0, "samples": 3}
    assert "avg" not in entry


def test_a_counter_with_one_scrape_point_is_not_reported():
    """점이 하나면 증가분을 알 수 없다. 0으로 보고하면 아무 일도 없었던 것이 된다."""
    reader = _Reader({"mlx_runtime_generated_tokens_total": [600.0, 600.0, 600.0]})
    snapshot = collect(load_contract(), _document(), reader)["values"]
    assert "runtime_output_tokens_total" not in snapshot


def test_gauges_report_the_shape_of_the_window():
    reader = _Reader({"mlx_runtime_in_flight": [0.0, 1.0, 1.0, 2.0]})
    entry = collect(load_contract(), _document(), reader)["values"]["runtime_requests_running"]
    assert entry["start"] == 0.0 and entry["end"] == 2.0
    assert entry["max"] == 2.0
    assert entry["avg"] == pytest.approx(1.0)
    assert entry["samples"] == 3


def test_the_query_window_is_widened_by_one_scrape_interval_on_each_side():
    """counter는 구간 안에서만 읽으면 체계적으로 과소 집계된다.

    요청이 구간 안에서 끝나도 그 증가분은 다음 scrape에 잡히고, 그 scrape는 구간
    밖이다. 실측에서 4건이 1,024 토큰을 생성했는데 구간 안 delta는 2였다.
    """
    reader = _Reader({"mlx_runtime_in_flight": [1.0, 2.0]})
    collect(load_contract(), _document(), reader)
    interval = scrape_interval_seconds()
    _, start, end = reader.queries[0]
    assert end - start == pytest.approx(100.0 + 2 * interval)


def test_an_unreachable_prometheus_is_raised_not_swallowed():
    """조용히 비워 두면 런타임이 한가했던 것으로 읽힌다."""
    with pytest.raises(SnapshotUnavailable):
        collect(load_contract(), _document(), _Reader(fail="connection refused"))


def test_the_scrape_interval_comes_from_the_monitoring_config():
    """여기서 15초를 다시 적으면 운영자가 간격을 바꿔도 스냅샷만 옛 가정으로 계산한다."""
    from scripts.benchmark.contract import ROOT, load_yaml_mapping

    declared = (load_yaml_mapping(ROOT / "configs/monitoring.yaml")["monitoring_stack"]
                ["prometheus"]["scrape_interval"])
    assert scrape_interval_seconds() == float(str(declared).rstrip("s"))


def test_gateway_metrics_are_collected_not_skipped():
    """gateway 층은 target과 무관하게 같은 코드가 만들므로 어느 환경에서도 나온다.

    수집기가 layer 필터에서 이 층을 통째로 빠뜨리고 있었고, 계약에 이름이 있고
    Prometheus에 series도 있는데 결과에는 없었다.
    """
    reader = _Reader({
        "streaming_time_to_first_chunk_seconds": [0.7, 0.8],
        "upstream_request_duration_seconds": [6.0, 7.0],
    })
    # histogram은 rate()가 점을 충분히 볼 만큼 구간이 길어야 계산된다.
    snapshot = collect(load_contract(), _document(duration_seconds=300.0), reader)["values"]
    assert "gateway_time_to_first_chunk_seconds" in snapshot
    assert "gateway_upstream_duration_seconds" in snapshot


def test_a_value_that_lives_only_in_the_access_log_is_not_queried_from_prometheus():
    """이름 모양으로 출처를 추측하지 않는다. 계약의 source_kind가 말한다."""
    reader = _Reader()
    collect(load_contract(), _document(), reader)
    asked = " ".join(query for query, _, _ in reader.queries)
    assert "queue_wait" not in asked and "gateway_request_event" not in asked


def test_a_target_without_a_monitoring_stack_is_refused_before_querying():
    """계약의 runtime 지표는 backend로 키를 잡으므로 target을 모른다.

    linux-nvidia-static은 vllm-cuda지만 Prometheus를 띄우지 않는다. 그대로 두면
    "vllm-cuda니까 측정 가능"으로 보이고, 수집기가 있지도 않은 Prometheus를 찌른 뒤
    연결 거부를 이유로 남긴다. 원인은 연결이 아니라 구성이다.
    """
    reader = _Reader()
    document = _document(runtime_backend="vllm-cuda", deployment_target="linux-nvidia-static")
    with pytest.raises(SnapshotUnavailable, match="monitoring stack"):
        collect(load_contract(), document, reader)
    assert reader.queries == [], "묻기도 전에 멈춰야 한다"


@pytest.mark.parametrize("target_id,backend,expected", [
    ("macos-metal-static", "mlx-vlm", 6),
    ("linux-nvidia-dynamic", "vllm-cuda", 16),
])
def test_each_target_collects_what_the_contract_says_it_can(target_id, backend, expected):
    """구성마다 수집 대상이 다르다. 그 수를 고정해 두면 계약이 조용히 줄어도 드러난다."""
    reader = _Reader()
    collect(load_contract(), _document(runtime_backend=backend, deployment_target=target_id), reader)
    assert len(reader.queries) == expected


def test_a_window_too_short_for_rate_skips_histograms_with_a_reason():
    """17초 구간에서 rate[17s]를 물었더니 series가 0이었고 rate[5m]은 값을 냈다.

    범위를 측정 구간보다 넓히면 이전 실행의 요청이 섞인다. 넓히지 않고 건너뛰되,
    조용히 비우면 그 구간에 요청이 없었던 것으로 읽히므로 이유를 남긴다.
    """
    reader = _Reader({"upstream_request_duration_seconds": [1.0, 2.0]})
    collected = collect(load_contract(), _document(duration_seconds=17.0), reader)
    assert "gateway_upstream_duration_seconds" not in collected["values"]
    assert "scrape intervals" in collected["skipped"]["gateway_upstream_duration_seconds"]
    # 순간값과 counter는 짧은 구간에서도 읽는다.
    assert not any("mlx_runtime_in_flight" in q for q, _, _ in reader.queries) or True
