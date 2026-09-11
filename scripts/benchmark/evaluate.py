"""원시 샘플에서 집계값을 만들고 SLO를 판정한다(ADR-0026 9·10절).

runner가 남긴 요청별 샘플만 입력으로 쓴다. Prometheus histogram을 판정 근거로
쓰지 않는다 -- bucket 경계가 percentile을 결정해 버리기 때문이다.

계산 방법은 계약이 소유한다. 같은 샘플로 도구마다 다른 percentile이 나오면
판정이 도구에 달린다.
"""
from __future__ import annotations

import math
from typing import Any

from scripts.benchmark.contract import PerformanceContract

# 계약이 선언한 통계만 계산한다. 이름이 늘면 여기서 막힌다.
_PERCENTILE_VALUE = {"p50": 0.50, "p95": 0.95, "p99": 0.99}


class EvaluationError(RuntimeError):
    """계약이 요구한 것을 계산할 수 없는 상태."""


def _statistics_policy(contract: PerformanceContract) -> dict[str, Any]:
    policy = contract.statistics
    method = str(policy.get("percentile_method", ""))
    if method != "nearest_rank":
        raise EvaluationError(
            f"slo.yaml declares percentile_method={method!r}; this evaluator implements "
            "nearest_rank only. 다른 방법으로 계산하면 계약이 선언한 판정과 달라진다."
        )
    return policy


def percentile(values: list[float], name: str) -> float:
    """정렬 후 ceil(p x n)번째 값. 보간하지 않는다.

    "요청의 95%가 이 값보다 빠르다"는 SLO 문장이 그대로 성립한다.
    """
    if not values:
        raise EvaluationError("percentile of an empty sample")
    ordered = sorted(values)
    rank = max(1, math.ceil(_PERCENTILE_VALUE[name] * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _distribution(values: list[float], policy: dict[str, Any], allowed: set[str] | None) -> dict[str, Any]:
    """표본 하나의 분포 요약. 표본이 모자란 percentile은 숫자를 내지 않는다."""
    summary: dict[str, Any] = {"count": len(values)}
    if not values:
        return summary
    minimum = policy.get("minimum_samples") or {}
    for name in _PERCENTILE_VALUE:
        if allowed is not None and name not in allowed:
            continue
        # 20개로 p99를 내면 사실상 최댓값이다. 그 값을 임계값과 비교하면 판정이
        # 표본 수에 달린다. 못 채우면 None을 남겨 not_evaluated로 흐르게 한다.
        summary[name] = percentile(values, name) if len(values) >= int(minimum.get(name, 1)) else None
    if allowed is None or "mean" in allowed:
        summary["mean"] = sum(values) / len(values)
    if allowed is None or "min" in allowed:
        summary["min"] = min(values)
    if allowed is None or "max" in allowed:
        summary["max"] = max(values)
    return summary


def _sample_values(requests: list[dict[str, Any]], field: str) -> list[float]:
    """성공한 요청에서만 모은다.

    거절된 요청의 소요 시간은 거절까지 걸린 시간이지 응답 지연이 아니다. 섞으면
    부하를 많이 버릴수록 지연이 좋아 보인다.
    """
    out: list[float] = []
    for request in requests:
        if not request.get("succeeded"):
            continue
        value = request.get(field)
        if isinstance(value, (int, float)):
            out.append(float(value))
        elif isinstance(value, list):
            out.extend(float(item) for item in value)
    return out


def summarize(document: dict[str, Any], contract: PerformanceContract) -> dict[str, Any]:
    policy = _statistics_policy(contract)
    metrics = contract.metrics
    requests = document["requests"]
    succeeded = [request for request in requests if request.get("succeeded")]
    window = float(document["run"]["dispatch_window_seconds"])
    summary: dict[str, Any] = {}

    for name, metric in metrics.items():
        if metric["layer"] != "client":
            continue
        unit = metric.get("aggregation_unit")
        if unit == "request":
            summary[name] = _distribution(_sample_values(requests, name), policy, None)
        elif unit == "streamed_output_gap":
            # 전송 계층이 chunk를 묶어 보내면 percentile이 서버가 아니라 묶임을 잰다.
            allowed = set(metric.get("interpretable_statistics") or [])
            values = _sample_values(requests, name)
            summary[name] = _distribution(values, policy, allowed - {"sum"})
            if "sum" in allowed and values:
                summary[name]["value"] = sum(values)

    # 창 단위 지표는 계산이 제각각이라 이름을 코드가 안다. 계약이 이름을 바꾸면
    # 예전 이름으로 값을 내고 판정은 "측정값 없음"이 된다 -- 실제로 측정된 값인데도.
    # 그래서 계약과 이 표가 정확히 같은 집합인지 먼저 확인한다.
    declared_window = {
        name for name, metric in metrics.items()
        if metric["layer"] == "client" and metric.get("aggregation_unit") == "window"
    }
    computed = {
        "client_request_success_ratio": lambda: {
            "value": (len(succeeded) / len(requests)) if requests else None,
            "count": len(requests),
        },
        "client_output_tokens_per_second": lambda: {
            "value": _rate(succeeded, ("client_output_tokens",), window), "count": len(succeeded),
        },
        "client_total_tokens_per_second": lambda: {
            "value": _rate(succeeded, ("client_input_tokens", "client_output_tokens"), window),
            "count": len(succeeded),
        },
        "client_goodput_requests_per_second": lambda: _goodput(
            document, contract, succeeded, window
        ),
    }
    if declared_window != set(computed):
        raise EvaluationError(
            "contract window metrics and evaluator disagree; "
            f"계약에만 있음: {sorted(declared_window - set(computed))}, "
            f"코드에만 있음: {sorted(set(computed) - declared_window)}"
        )
    for name, compute in computed.items():
        summary[name] = compute()
    return summary


def _rate(requests: list[dict[str, Any]], fields: tuple[str, ...], window: float) -> float | None:
    if window <= 0:
        return None
    total = sum(int(request.get(field) or 0) for request in requests for field in fields)
    return total / window


def _objectives(slo: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return dict(slo.get("objectives") or {})


def _threshold(objective: dict[str, Any], document: dict[str, Any]) -> float | None:
    """이 실행 환경에 해당하는 임계값.

    측정으로 그은 선은 target마다 다르다. 26B MLX의 토큰당 시간과 GPU의 12B FP8은
    같을 수 없다. 한 숫자를 모두에 걸면 한쪽에서는 반드시 틀린 판정을 낸다.
    측정하지 않은 target은 비어 있고, 그때는 판정하지 않는다.
    """
    if "threshold" in objective:
        return float(objective["threshold"])
    by_target = objective.get("thresholds_by_target") or {}
    target = str((document.get("environment") or {}).get("deployment_target", ""))
    value = by_target.get(target)
    return float(value) if value is not None else None


def _goodput(
    document: dict[str, Any],
    contract: PerformanceContract,
    succeeded: list[dict[str, Any]],
    window: float,
) -> dict[str, Any]:
    """workload의 모든 SLO를 만족한 요청만 센 초당 처리량.

    계약의 정의가 그렇다. 성공했지만 SLO를 넘긴 요청은 세지 않는다. 따라서
    임계값이 없으면 계산할 수 없고, 성공률로 대신하지 않는다.
    """
    found = contract.slo_class_for(document["workload"]["id"])
    if found is None:
        return {"value": None, "count": len(succeeded)}
    _, slo = found
    metrics = contract.metrics
    # 요청 하나하나를 판정할 수 있는 지표만 쓴다. 창 단위 집계값에는 요청별로
    # "이 요청이 SLO를 만족했는가"를 물을 수 없다.
    limits = [
        (name, threshold, str(metrics[name].get("better", "lower")))
        for name, objective in _objectives(slo).items()
        if metrics.get(name, {}).get("aggregation_unit") == "request"
        and (threshold := _threshold(objective, document)) is not None
    ]
    if not limits:
        # 임계값이 없으면 "SLO를 만족한 요청"이 정의되지 않는다. 성공 요청 수를
        # 대신 쓰면 정의가 다른 값을 같은 이름으로 내보내게 된다.
        return {"value": None, "count": len(succeeded)}
    good = [
        request for request in succeeded
        if all(_meets(request.get(name), limit, better) for name, limit, better in limits)
    ]
    return {"value": len(good) / window if window > 0 else None, "count": len(good)}


def judge(document: dict[str, Any], contract: PerformanceContract, summary: dict[str, Any]) -> dict[str, Any]:
    """SLO 판정. 임계값이 없으면 숫자만 기록하고 not_evaluated로 남긴다."""
    workload_id = document["workload"]["id"]
    found = contract.slo_class_for(workload_id)
    if found is None:
        raise EvaluationError(f"no SLO class declares workload {workload_id!r}")
    slo_name, slo = found

    metrics = contract.metrics
    objectives: list[dict[str, Any]] = []
    for metric_name, objective in _objectives(slo).items():
        metric = metrics[metric_name]
        # client 층 지표는 summarize가 전부 만든다. 없다면 계약과 코드가 어긋난
        # 것이지 "측정하지 못한" 것이 아니다. 그 둘을 같은 결과로 보고하면 안 된다.
        if metric["layer"] == "client" and metric_name not in summary:
            raise EvaluationError(
                f"slo objective {metric_name!r} has no summary entry; 계약이 선언한 "
                "지표를 집계가 만들지 않았다"
            )
        better = str(metric.get("better", "lower"))
        entry_summary = summary.get(metric_name) or {}
        for statistic in _requested_statistics(objective):
            observed = _observed(entry_summary, statistic)
            threshold = _threshold(objective, document)
            entry: dict[str, Any] = {
                "metric": metric_name,
                "statistic": statistic,
                "observed": observed,
                "threshold": threshold,
                "threshold_source": objective.get("source"),
                "status": _status(observed, threshold, better),
            }
            if entry["status"] == "not_evaluated":
                entry["not_evaluated_reason"] = _reason(metric, entry_summary, statistic, observed, threshold)
            objectives.append(entry)

    statuses = {entry["status"] for entry in objectives}
    if "fail" in statuses:
        overall = "fail"
    elif statuses == {"not_evaluated"} or not objectives:
        overall = "not_evaluated"
    else:
        overall = "pass"
    return {
        "slo_class": slo_name,
        "enforcement": str(slo["enforcement"]),
        "status": overall,
        "objectives": objectives,
    }


def _requested_statistics(objective: dict[str, Any]) -> list[str]:
    statistics = list(objective.get("percentiles") or [])
    if "aggregate" in objective:
        statistics.append(str(objective["aggregate"]))
    return statistics or ["value"]


def _observed(summary_entry: dict[str, Any], statistic: str) -> float | None:
    """창 단위 집계값은 실행 하나에 값이 하나다.

    ``aggregate: min``은 sweep의 여러 지점 중 최솟값을 뜻한다. 실행 하나를
    판정할 때는 그 하나의 값이 곧 최솟값이다.
    """
    if statistic in summary_entry:
        return summary_entry[statistic]
    if statistic in ("min", "max") and "value" in summary_entry:
        return summary_entry["value"]
    return None


def _reason(
    metric: dict[str, Any],
    summary_entry: dict[str, Any],
    statistic: str,
    observed: Any,
    threshold: Any,
) -> str:
    """왜 판정하지 못했는지. 고쳐야 할 것이 서로 다르다.

    표본이 모자란 것은 더 오래 돌리면 되고, 임계값이 없는 것은 정해야 한다.
    """
    if observed is None:
        if statistic in _PERCENTILE_VALUE and summary_entry.get("count"):
            return "insufficient_samples"
        # goodput처럼 계산 자체가 임계값을 요구하는 지표가 있다. 그때 "측정값 없음"은
        # 측정에 실패했다는 뜻으로 읽혀 엉뚱한 곳을 보게 만든다. 계약이 그 의존을
        # requires로 선언하므로 이름을 코드에 박지 않는다.
        if (metric.get("requires") or {}).get("slo_thresholds"):
            return "no_threshold"
        return "not_measured"
    return "no_threshold" if threshold is None else "not_measured"


def _meets(observed: Any, threshold: float, better: str) -> bool:
    if not isinstance(observed, (int, float)):
        return False
    return float(observed) >= threshold if better == "higher" else float(observed) <= threshold


def _status(observed: float | None, threshold: float | None, better: str) -> str:
    """지연은 낮을수록, 성공률과 처리량은 높을수록 좋다. 계약이 방향을 적는다."""
    if threshold is None or observed is None:
        return "not_evaluated"
    return "pass" if _meets(observed, float(threshold), better) else "fail"


def evaluate(document: dict[str, Any], contract: PerformanceContract) -> dict[str, Any]:
    """결과 문서에 summary와 verdict를 채워 돌려준다."""
    summary = summarize(document, contract)
    return {**document, "summary": summary, "verdict": judge(document, contract, summary)}
