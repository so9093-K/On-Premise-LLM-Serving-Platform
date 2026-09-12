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

from scripts.benchmark.contract import PerformanceContract
from scripts.benchmark.evaluate import evaluate
from scripts.benchmark.runner import RunOptions, RunnerError, run_workload

_SUPPORTED_SIGNALS = {"time_to_first_chunk_drift"}
# sweep이 실제로 읽는 규칙. 계약 검증기가 이 목록으로 선언 누락을 먼저 잡는다.
_REQUIRED_CRITERION_FIELDS = (
    "signal",
    "max_second_half_ratio",
    "require_success_ratio",
    "min_throughput_gain_ratio",
)


def required_criterion_fields() -> tuple[str, ...]:
    return _REQUIRED_CRITERION_FIELDS


def _criterion(contract: PerformanceContract) -> dict[str, Any]:
    criterion = contract.capacity_criterion
    signal = str(criterion.get("signal", ""))
    if signal not in _SUPPORTED_SIGNALS:
        raise RunnerError(
            f"contract declares capacity signal {signal!r}; this sweep implements "
            f"{sorted(_SUPPORTED_SIGNALS)} only"
        )
    return criterion


def sweep_axis(contract: PerformanceContract, workload_id: str) -> tuple[str, list[float]]:
    """무엇을 올려 가며 훑는지와 그 지점들.

    open loop은 요청률을, closed loop은 동시성을 올린다. 둘은 다른 것을 묻는다.
    요청률 sweep은 "이 부하를 감당하는가"를, 동시성 sweep은 "동시에 더 돌리면
    처리량이 더 나오는가"를 본다.
    """
    workload = contract.workload(workload_id)
    traffic = workload.get("traffic") or {}
    mode = str(traffic.get("mode", ""))
    # 길이 sweep이 선언돼 있으면 그것이 이 workload가 묻는 축이다. 부하가 아니라
    # "입력이 길어지면 어디까지 견디는가"를 본다.
    lengths = (workload.get("prompt") or {}).get("input_tokens_sweep") or []
    if len(lengths) > 1:
        return "input_tokens", sorted(float(value) for value in lengths)
    if mode == "open_loop":
        points = [float(rate) for rate in traffic.get("request_rate_sweep") or []]
        axis = "request_rate_per_second"
    elif mode == "closed_loop":
        points = [float(value) for value in traffic.get("concurrency_sweep") or []]
        axis = "concurrency"
    else:
        raise RunnerError(f"workload {workload_id!r} declares unsupported traffic mode {mode!r}")
    if not points:
        key = "request_rate_sweep" if mode == "open_loop" else "concurrency_sweep"
        raise RunnerError(
            f"workload {workload_id!r} ({mode}) declares no {key}; 훑을 지점이 없으면 "
            "부하를 올려 가며 볼 수 없다"
        )
    return axis, sorted(points)


def _declared_point(
    contract: PerformanceContract, workload_id: str, axis: str, points: list[float]
) -> float:
    """계약이 재려고 선언한 지점. sweep 결과를 이 값과 비교한다.

    요청률만 traffic에 단일 값으로 선언돼 있다(이 workload가 재려는 부하). 동시성과
    입력 길이는 sweep 목록이 곧 선언이므로 그 최댓값이 "여기까지 되기를 기대한다"에
    해당한다. 예전에는 존재하지 않는 키를 찾다 fallback으로 같은 답에 닿았는데,
    누가 traffic에 concurrency를 적는 순간 조용히 다른 값이 됐을 것이다.
    """
    if axis == "request_rate_per_second":
        traffic = contract.workload(workload_id).get("traffic") or {}
        return float(traffic["request_rate_per_second"])
    return float(max(points))


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


def _point_summary(document: dict[str, Any], criterion: dict[str, Any], axis: str) -> dict[str, Any]:
    requests = document["requests"]
    summary = document["summary"]
    success = summary["client_request_success_ratio"]["value"]
    traffic = document["workload"]["traffic"]
    point: dict[str, Any] = {
        "requests": len(requests),
        "success_ratio": success,
        "run_id": document["run"]["id"],
        "output_tokens_per_second": summary["client_output_tokens_per_second"]["value"],
    }
    if axis == "request_rate_per_second":
        point["request_rate_per_second"] = traffic["request_rate_per_second"]
        # open loop에서는 대기가 쌓이는지가 감당 여부다.
        ratio = drift_ratio(requests)
        limit = float(criterion["max_second_half_ratio"])
        required = float(criterion["require_success_ratio"])
        point["time_to_first_chunk_drift"] = ratio
        point["sustained"] = (
            success is not None and success >= required and ratio is not None and ratio <= limit
        )
    elif axis == "concurrency":
        point["concurrency"] = traffic["concurrency"]
        # 처리량이 더 나오는지는 지점 간 비교라 _mark_throughput_scaling이 채운다.
        # 여기서는 요청을 버리지 않았는지만 본다.
        point["sustained"] = success is not None and success >= float(criterion["require_success_ratio"])
    else:
        point["input_tokens"] = document["workload"]["input_tokens"]
        # 길이는 부하가 아니다. 대기가 쌓이는지도, 처리량이 더 나오는지도 묻지
        # 않는다. 이 길이를 실제로 처리했는지와 prefill이 얼마나 늘었는지만 본다.
        #
        # percentile이 아니라 평균이다. percentile은 판정용이라 최소 표본을
        # 요구하는데, 길이 sweep은 지점마다 요청이 길어 그 수를 채우려면 몇십 분이
        # 걸린다. 여기서 필요한 것은 합격 여부가 아니라 길이에 따른 곡선이고,
        # 평균이면 충분하다. 표본 수는 requests에 함께 남는다.
        ttfc = summary.get("client_time_to_first_chunk_seconds") or {}
        point["mean_time_to_first_chunk_seconds"] = ttfc.get("mean")
        point["sustained"] = success is not None and success >= float(criterion["require_success_ratio"])
    return point


def _mark_throughput_scaling(points: list[dict[str, Any]], criterion: dict[str, Any]) -> None:
    """동시성을 올려 처리량이 실제로 늘었는지 표시한다.

    closed loop에서 성공률만 보면 어떤 동시성이든 늘 "감당"으로 나온다. 실측에서
    동시성 1·2·3의 처리량이 42.4·42.1·42.6 tok/s로 편차 0.6%였는데도 세 지점이
    모두 감당으로 보고됐다. 런타임이 순차 처리하면 동시에 받아도 더 나오지 않는다.
    """
    minimum = float(criterion["min_throughput_gain_ratio"])
    previous: float | None = None
    for point in sorted(points, key=lambda item: item["concurrency"]):
        throughput = point.get("output_tokens_per_second")
        if throughput is None:
            point["throughput_gain_ratio"] = None
            point["sustained"] = False
        elif previous is None:
            # 가장 낮은 지점은 비교 대상이 없다. 요청을 버리지 않았으면 유효하다.
            point["throughput_gain_ratio"] = None
        else:
            gain = (throughput - previous) / previous if previous > 0 else None
            point["throughput_gain_ratio"] = gain
            point["sustained"] = point["sustained"] and gain is not None and gain >= minimum
        if throughput is not None:
            previous = max(previous or 0.0, throughput)


def run_sweep(
    contract: PerformanceContract,
    options: RunOptions,
    *,
    on_point=None,
    refine_steps: int = 2,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """선언된 지점을 낮은 쪽부터 훑고, 경계 구간을 좁힌다.

    용량 요약과 지점별 결과 문서를 따로 돌려준다. 한 문서에 섞으면 지점마다
    schema가 요구하는 것이 달라진다.

    포화된 뒤에도 계속 올린다. 더 높은 부하에서 무슨 일이 생기는지가 용량만큼
    중요하고, 중간에 멈추면 그 구간이 결과에 남지 않는다.

    선언된 지점만으로는 용량이 "누군가 고른 눈금" 해상도로만 나온다. 실측에서
    지점이 0.1과 0.2뿐이라 용량이 0.1로 보고됐지만 실제 경계는 0.167이었다.
    그 차이가 중요한 이유는 첫 응답 시간이 용량 아래에서도 평평하지 않기
    때문이다 -- 요청 간격이 벌어지면 기기가 유휴 상태로 내려갔다 올라오는 비용이
    붙어 0.10 rps의 p50(0.88s)이 0.15 rps(0.66s)보다 오히려 나빴다. 경계를
    못 찾으면 기준선을 그 느린 구간에서 재게 된다.
    """
    criterion = _criterion(contract)
    sweep_id = options.sweep_id or f"{options.workload_id}-sweep-{uuid.uuid4().hex[:8]}"
    axis, declared_points = sweep_axis(contract, options.workload_id)
    points: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []

    def measure(value: float) -> dict[str, Any]:
        knob = {
            "request_rate_per_second": {"request_rate_per_second": value},
            "concurrency": {"concurrency": int(value)},
            "input_tokens": {"input_tokens": int(value)},
        }[axis]
        document = evaluate(
            run_workload(contract, replace(options, sweep_id=sweep_id, **knob)),
            contract,
        )
        documents.append(document)
        point = _point_summary(document, criterion, axis)
        points.append(point)
        if on_point is not None:
            # 지점이 끝나는 대로 호출자에게 넘긴다. 끝까지 모아 두면 여기서 무엇이
            # 잘못될 때 이미 측정한 몇 분치가 통째로 사라진다.
            on_point(point, axis, document)
        return point

    for value in declared_points:
        measure(value)

    # 요청률 sweep만 경계를 좁힌다. 동시성은 정수라 사이에 지점이 없고, 계약이
    # admission 한도까지만 선언하므로 그 목록이 이미 전부다.
    if axis == "request_rate_per_second":
        for _ in range(refine_steps):
            low = max((p[axis] for p in points if p["sustained"]), default=None)
            high = min(
                (p[axis] for p in points
                 if not p["sustained"] and (low is None or p[axis] > low)),
                default=None,
            )
            if low is None or high is None:
                break
            middle = round((low + high) / 2, 4)
            if any(abs(p[axis] - middle) < 1e-6 for p in points):
                break
            measure(middle)

    points.sort(key=lambda point: point[axis])
    if axis == "concurrency":
        _mark_throughput_scaling(points, criterion)
    sustained = [point for point in points if point["sustained"]]
    environment = documents[0]["environment"] if documents else {}
    return {
        "schema_version": 1,
        "sweep_id": sweep_id,
        "workload_id": options.workload_id,
        # 용량은 구성마다 다르다. 어떤 모델을 어떤 가속기에서 돌린 결과인지 없으면
        # 이 숫자를 다른 장비에 잘못 적용하게 된다.
        "environment": {
            key: environment[key]
            for key in ("runtime_backend", "model_id", "model_revision", "deployment_target", "gpu")
            if key in environment
        },
        "criterion": criterion,
        "axis": axis,
        "points": points,
        # 감당한 지점 중 가장 높은 것. 하나도 없으면 가장 낮은 지점조차 넘어선 것이다.
        "sustained_point": (max(point[axis] for point in sustained) if sustained else None),
        "declared_point": _declared_point(contract, options.workload_id, axis, declared_points),
    }, documents
