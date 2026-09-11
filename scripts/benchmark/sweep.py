"""요청률을 훑어 이 구성이 감당하는 부하를 찾는다(ADR-0026 7절).

지점마다 결과 문서를 따로 만든다. 한 문서에 여러 부하의 샘플을 담으면 percentile이
서로 다른 조건의 값을 섞어 계산되고, 그 숫자는 어느 부하도 설명하지 못한다.

용량 판정 기준은 계약이 소유한다(configs/performance/workloads.yaml의
capacity_criterion). 여기서 규칙을 다시 정하지 않는다.
"""
from __future__ import annotations

import statistics
import uuid
from dataclasses import replace
from typing import Any

from scripts.benchmark.contract import WORKLOADS_PATH, PerformanceContract, load_yaml_mapping
from scripts.benchmark.evaluate import evaluate
from scripts.benchmark.runner import RunOptions, RunnerError, run_workload

_SUPPORTED_SIGNALS = {"time_to_first_chunk_drift"}


def _criterion() -> dict[str, Any]:
    criterion = load_yaml_mapping(WORKLOADS_PATH).get("capacity_criterion") or {}
    signal = str(criterion.get("signal", ""))
    if signal not in _SUPPORTED_SIGNALS:
        raise RunnerError(
            f"contract declares capacity signal {signal!r}; this sweep implements "
            f"{sorted(_SUPPORTED_SIGNALS)} only"
        )
    return criterion


def rate_points(contract: PerformanceContract, workload_id: str) -> list[float]:
    traffic = (contract.workload(workload_id).get("traffic") or {})
    points = [float(rate) for rate in traffic.get("request_rate_sweep") or []]
    if not points:
        raise RunnerError(
            f"workload {workload_id!r} declares no request_rate_sweep; 훑을 지점이 없으면 "
            "용량을 찾을 수 없다"
        )
    return sorted(points)


def drift_ratio(samples: list[dict[str, Any]]) -> float | None:
    """측정 구간 뒤쪽 절반의 첫 응답 중앙값이 앞쪽의 몇 배인가.

    안정된 계에서는 요청 순서와 첫 응답 시간이 무관해 1에 가깝다. 포화되면 뒤쪽
    요청일수록 앞의 요청을 기다려 비율이 커진다. 중앙값을 쓰는 것은 평균과
    최댓값이 한 건의 튐에 흔들리기 때문이다.
    """
    values = [
        float(sample["client_time_to_first_chunk_seconds"])
        for sample in samples
        if sample.get("succeeded") and sample.get("client_time_to_first_chunk_seconds") is not None
    ]
    if len(values) < 4:
        return None
    half = len(values) // 2
    first = statistics.median(values[:half])
    second = statistics.median(values[half:])
    return second / first if first > 0 else None


def _point_summary(document: dict[str, Any], criterion: dict[str, Any]) -> dict[str, Any]:
    requests = document["requests"]
    success = document["summary"]["client_request_success_ratio"]["value"]
    ratio = drift_ratio(requests)
    limit = float(criterion["max_second_half_ratio"])
    required = float(criterion["require_success_ratio"])
    sustained = (
        success is not None
        and success >= required
        and ratio is not None
        and ratio <= limit
    )
    return {
        "request_rate_per_second": document["workload"]["traffic"]["request_rate_per_second"],
        "requests": len(requests),
        "success_ratio": success,
        "time_to_first_chunk_drift": ratio,
        "sustained": sustained,
        "run_id": document["run"]["id"],
    }


def run_sweep(
    contract: PerformanceContract,
    options: RunOptions,
    *,
    on_point=None,
    refine_steps: int = 2,
) -> dict[str, Any]:
    """선언된 지점을 낮은 쪽부터 훑고, 경계 구간을 좁힌다.

    포화된 뒤에도 계속 올린다. 더 높은 부하에서 무슨 일이 생기는지가 용량만큼
    중요하고, 중간에 멈추면 그 구간이 결과에 남지 않는다.

    선언된 지점만으로는 용량이 "누군가 고른 눈금" 해상도로만 나온다. 실측에서
    지점이 0.1과 0.2뿐이라 용량이 0.1로 보고됐지만 실제 경계는 0.167이었다.
    그 차이가 중요한 이유는 첫 응답 시간이 용량 아래에서도 평평하지 않기
    때문이다 -- 요청 간격이 벌어지면 기기가 유휴 상태로 내려갔다 올라오는 비용이
    붙어 0.10 rps의 p50(0.88s)이 0.15 rps(0.66s)보다 오히려 나빴다. 경계를
    못 찾으면 기준선을 그 느린 구간에서 재게 된다.
    """
    criterion = _criterion()
    sweep_id = options.sweep_id or f"{options.workload_id}-sweep-{uuid.uuid4().hex[:8]}"
    points: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []

    def measure(rate: float) -> dict[str, Any]:
        document = evaluate(
            run_workload(
                contract,
                replace(options, request_rate_per_second=rate, sweep_id=sweep_id),
            ),
            contract,
        )
        documents.append(document)
        point = _point_summary(document, criterion)
        points.append(point)
        if on_point is not None:
            on_point(point)
        return point

    for rate in rate_points(contract, options.workload_id):
        measure(rate)

    # 감당한 가장 높은 지점과 무너진 가장 낮은 지점 사이를 이분한다. 두 번이면
    # 구간이 1/4로 줄어 눈금 사이에 숨은 경계를 찾는다.
    for _ in range(refine_steps):
        low = max((p["request_rate_per_second"] for p in points if p["sustained"]), default=None)
        high = min(
            (p["request_rate_per_second"] for p in points
             if not p["sustained"] and (low is None or p["request_rate_per_second"] > low)),
            default=None,
        )
        if low is None or high is None:
            break
        middle = round((low + high) / 2, 4)
        if any(abs(p["request_rate_per_second"] - middle) < 1e-6 for p in points):
            break
        measure(middle)

    points.sort(key=lambda point: point["request_rate_per_second"])
    sustained = [point for point in points if point["sustained"]]
    return {
        "sweep_id": sweep_id,
        "workload_id": options.workload_id,
        "criterion": criterion,
        "points": points,
        # 감당한 지점 중 가장 높은 것. 하나도 없으면 가장 낮은 지점조차 넘어선 것이다.
        "sustained_rate_per_second": (
            max(point["request_rate_per_second"] for point in sustained) if sustained else None
        ),
        "declared_rate_per_second": float(
            (contract.workload(options.workload_id).get("traffic") or {})
            .get("request_rate_per_second", 0)
        ),
        "documents": documents,
    }
