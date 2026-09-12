"""릴리스 자격 게이트를 고정한다(ADR-0026 10·14절).

게이트는 측정하지 않고 판정만 한다. 측정과 판정을 한 명령에 묶으면 판정을 다시
하려고 몇 분짜리 측정을 다시 돌려야 한다.
"""

from __future__ import annotations

import pytest

from scripts.benchmark.contract import load_contract
from scripts.benchmark.gate import GateError, judge


def _document(*, run_id="r", regression=None, verdict=None) -> dict:
    document = {"run": {"id": run_id}}
    if regression is not None:
        document["regression"] = regression
    if verdict is not None:
        document["verdict"] = verdict
    return document


def _regression(status: str) -> dict:
    return {
        "baseline_run_ids": ["base"],
        "status": status,
        "comparisons": [{
            "metric": "client_time_to_first_chunk_seconds", "statistic": "p95",
            "baseline": 0.9, "observed": 1.1, "change_ratio": 0.22,
            "tolerance_ratio": 0.1, "status": status,
        }],
    }


def _verdict(enforcement: str, status: str) -> dict:
    return {
        "slo_class": "interactive", "enforcement": enforcement, "status": status,
        "objectives": [{
            "metric": "client_time_to_first_chunk_seconds", "statistic": "p95",
            "observed": 2.0, "threshold": 1.0, "status": status,
        }],
    }


def test_a_regression_blocks_the_release():
    """회귀 판정을 만들어 두고 아무것도 막지 않으면 결과 파일 안에서만 존재한다."""
    outcome = judge([_document(regression=_regression("regressed"))], load_contract())
    assert outcome["status"] == "fail"
    assert outcome["blocked"][0]["check"] == "regression"


def test_a_clean_run_passes():
    outcome = judge([_document(regression=_regression("ok"))], load_contract())
    assert outcome["status"] == "pass"
    assert outcome["blocked"] == []


def test_an_slo_failure_below_release_does_not_block():
    """새 SLO는 observe에서 시작한다. 보고만 하고 실행을 실패시키지 않는다."""
    outcome = judge(
        [_document(regression=_regression("ok"), verdict=_verdict("observe", "fail"))],
        load_contract(),
    )
    assert outcome["status"] == "pass"
    assert any(e["check"] == "slo" and e["status"] == "fail" for e in outcome["checked"])


@pytest.mark.parametrize("enforcement", ["release", "required"])
def test_an_slo_failure_at_or_above_release_blocks(enforcement):
    outcome = judge(
        [_document(regression=_regression("ok"), verdict=_verdict(enforcement, "fail"))],
        load_contract(),
    )
    assert outcome["status"] == "fail"
    assert outcome["blocked"][0]["check"] == "slo"


def test_a_gate_that_could_check_nothing_is_refused():
    """확인하지 못한 것을 통과로 세면 baseline을 지우거나 임계값을 비우는 것만으로
    게이트를 무력화할 수 있다."""
    with pytest.raises(GateError, match="no performance check"):
        judge([_document()], load_contract())


def test_no_results_at_all_is_refused():
    with pytest.raises(GateError, match="no benchmark results"):
        judge([], load_contract())


def test_a_missing_baseline_is_reported_rather_than_counted_as_passing():
    """새 구성에는 baseline이 없다. 그 사실이 보여야 한다."""
    outcome = judge(
        [_document(regression=None, verdict=_verdict("observe", "pass"))], load_contract()
    )
    assert outcome["status"] == "pass"
    assert any(s["check"] == "regression" and "baseline" in s["reason"] for s in outcome["skipped"])


def test_an_slo_with_no_thresholds_is_skipped_not_passed():
    """임계값이 없는 판정을 통과로 세면 게이트가 아무것도 보지 않는다."""
    verdict = {"slo_class": "interactive", "enforcement": "release", "status": "not_evaluated",
               "objectives": [{"metric": "m", "statistic": "p95", "status": "not_evaluated"}]}
    outcome = judge([_document(regression=_regression("ok"), verdict=verdict)], load_contract())
    assert any(s["check"] == "slo" and "임계값" in s["reason"] for s in outcome["skipped"])
    assert outcome["status"] == "pass"


def test_an_unknown_enforcement_level_is_refused(monkeypatch):
    """사다리에 없는 단계를 만나면 조용히 통과시키지 않는다."""
    from scripts.benchmark import gate

    monkeypatch.setattr(gate, "load_yaml_mapping", lambda path: {"enforcement": "maybe"})
    with pytest.raises(GateError, match="unknown enforcement"):
        judge([_document(regression=_regression("ok"))], load_contract())
