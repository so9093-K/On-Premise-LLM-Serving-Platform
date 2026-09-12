"""결과에서 뽑는 보고서를 고정한다(ADR-0026 9절).

원본은 JSON이고 보고서는 파생이다. 여기서 숫자를 다시 계산하지 않는다 -- 계산하면
보고서와 결과 파일이 다른 숫자를 말하게 된다.
"""

from __future__ import annotations

from scripts.benchmark.contract import load_contract
from scripts.benchmark.report import render


def _document(**extra) -> dict:
    document = {
        "run": {"id": "run-1", "started_at": "2026-01-01T00:00:00Z", "duration_seconds": 206.0},
        "environment": {
            "runtime_backend": "mlx-vlm", "runtime_profile": "p",
            "model_id": "org/model", "model_revision": "0e3cbab38ce5",
            "git_commit": "44c9f2b1146a0000",
            "gpu": {"model": "Apple M5", "count": 1, "memory_kind": "unified"},
        },
        "workload": {"id": "interactive", "cache_policy": "cold", "input_tokens": 512,
                     "traffic": {"mode": "open_loop", "request_rate_per_second": 0.1}},
        "requests": [{"index": 0, "succeeded": True}],
        "summary": {
            "client_time_to_first_chunk_seconds": {"p50": 0.8723, "p95": 0.8922, "mean": 0.8618,
                                                   "count": 21},
            "client_time_per_output_chunk_seconds": {"mean": 0.0206, "value": 109.5, "count": 5313},
            "client_request_success_ratio": {"value": 1.0, "count": 21},
        },
    }
    document.update(extra)
    return document


def test_a_single_value_never_appears_in_a_percentile_column():
    """chunk 간격의 합계 109.5초가 p50 칸에 나왔고, 그 자리에서는 chunk 하나당
    시간으로 읽힌다."""
    rendered = render(_document(), load_contract())
    distribution = rendered[rendered.index("### 분포"):rendered.index("### 단일 값")]
    assert "109.5" not in distribution
    single = rendered[rendered.index("### 단일 값"):]
    assert "109.5" in single
    # 같은 지표의 평균과 합계가 각각 제 이름으로 나온다.
    assert "평균" in single and "합계" in single


def test_numbers_come_from_the_result_not_from_a_recomputation():
    """보고서가 다시 계산하면 결과 파일과 다른 숫자를 말하게 된다."""
    document = _document()
    document["summary"]["client_time_to_first_chunk_seconds"]["p50"] = 1.2345
    assert "1.234" in render(document, load_contract())


def test_a_missing_baseline_is_stated_rather_than_omitted():
    """비교하지 않은 것을 비워 두면 통과로 읽힌다."""
    rendered = render(_document(), load_contract())
    assert "baseline이 없습니다" in rendered
    assert "통과가 아닙니다" in rendered


def test_a_skipped_runtime_metric_says_why():
    rendered = render(_document(
        runtime_snapshot={"runtime_requests_running": {"start": 1, "end": 0, "avg": 0.5,
                                                       "max": 1, "samples": 2}},
        runtime_snapshot_skipped={"gateway_upstream_duration_seconds": "window too short"},
    ), load_contract())
    assert "건너뜀" in rendered and "window too short" in rendered


def test_an_unreadable_runtime_snapshot_says_why():
    rendered = render(_document(runtime_snapshot_error="cannot reach Prometheus"), load_contract())
    assert "읽지 못했습니다" in rendered and "cannot reach Prometheus" in rendered


def test_the_measurement_conditions_identify_the_configuration():
    """6개월 뒤 이 숫자를 해석하려면 무엇을 어디서 쟀는지가 있어야 한다."""
    rendered = render(_document(), load_contract())
    for expected in ("mlx-vlm", "Apple M5", "0.1 rps", "512", "0e3cbab3", "44c9f2b1146a"):
        assert expected in rendered, expected
