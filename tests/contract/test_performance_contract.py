"""성능 계약과 benchmark 실행 코드의 cross-boundary 정합을 고정한다(ADR-0026).

Repository contract 자체의 정합성은
`scripts/validation/validate_performance_contract.py`와 `make validate`가 소유한다.
Pytest는 같은 canonical 입력으로 validator를 다시 실행하지 않고, validator가 YAML만
읽어서는 확인할 수 없는 실행 코드와의 연결만 보호한다.
"""

from __future__ import annotations

from scripts.validation import validate_performance_contract as contract


def test_every_primary_metric_has_a_producer_in_the_benchmark_client():
    """계약이 primary로 선언한 요청 단위 지표를 runner가 실제로 만들어야 한다.

    검증기는 YAML만 읽으므로 이 정합을 볼 수 없다. client_time_per_output_token_seconds는
    계약에만 있고 만드는 곳이 없었고, 그 상태로 두면 판정 근거가 빈 채로 통과한다.
    """
    from scripts.benchmark.client import RequestSample

    document = contract.load_yaml_mapping(contract.WORKLOADS_PATH)
    metrics = contract.load_yaml_mapping(contract.METRICS_PATH)["metrics"]
    # 성공한 요청이 실제로 내보내는 문서만 본다. dataclass의 필드 목록을 함께 보면
    # 값이 문서에서 빠져도 테스트가 통과한다 -- 처음 쓴 판이 그래서 무력했다.
    produced = set(RequestSample(
        index=0, succeeded=True, status_code=200,
        client_time_to_first_chunk_seconds=0.5,
        client_operation_duration_seconds=2.0,
        client_time_per_output_chunk_seconds=[0.01, 0.01],
        client_input_tokens=512, client_output_tokens=64,
    ).as_document())

    per_request = {
        name for name, metric in metrics.items()
        if metric["layer"] == "client"
        and metric.get("aggregation_unit") in ("request", "streamed_output_gap")
    }
    for workload in document["workloads"].values():
        for name in workload["primary_metrics"]:
            if name in per_request:
                assert name in produced, f"{name} is declared primary but nothing produces it"
