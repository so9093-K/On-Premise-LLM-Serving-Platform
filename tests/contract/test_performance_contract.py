"""성능 계약이 저장소의 다른 기준과 갈라지지 않는지 고정한다(ADR-0026).

계약 규칙 자체는 scripts/validation/validate_performance_contract.py가 소유한다.
같은 규칙을 여기에도 적으면 한 규칙을 바꿀 때 두 곳을 고쳐야 하고, 한쪽만 고치면
검증기와 테스트가 서로 다른 계약을 주장한다. 실제로 드리프트 8종을 주입해 봤더니
7종을 양쪽이 똑같이 잡았다 -- 얻는 것 없이 유지 비용만 두 배였다.

그래서 여기에는 두 가지만 남긴다. 검증기가 통과하는지, 그리고 검증기가 확인할 수
없는 것(실행 코드와의 정합)이다.
"""

from __future__ import annotations

from scripts.validation import validate_performance_contract as contract


def test_the_contract_validator_passes_on_the_repository_contract():
    """진입점이 하나여야 호출자가 일부만 검증하고 통과했다고 믿지 않는다."""
    failures: list[str] = []
    contract.validate(failures)
    assert failures == []


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
