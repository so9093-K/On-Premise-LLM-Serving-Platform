"""성능 지표 계약이 저장소의 다른 기준과 갈라지지 않는지 고정한다(ADR-0026).

이 계약은 metric 이름을 단독 소유한다. 이름이 실재하지 않는 것을 가리키면
benchmark와 dashboard가 조용히 빈 값을 받으므로, 참조 무결성을 테스트로 고정한다.
"""

from __future__ import annotations

import pytest

from scripts.validation import validate_performance_contract as contract


def test_metric_contract_matches_targets_rules_and_exporters():
    failures: list[str] = []
    contract._validate_metrics(failures)
    assert failures == []


def test_client_and_gateway_layers_are_target_independent():
    """공유 코드가 만드는 층에 target별 projection이 생기면 결정이 무너진 것이다."""
    document = contract.load_yaml_mapping(contract.METRICS_PATH)
    for name, metric in document["metrics"].items():
        if metric["layer"] in ("client", "gateway"):
            assert "backends" not in metric, name
            assert "targets" not in metric, name


def test_every_runtime_metric_declares_all_backends():
    document = contract.load_yaml_mapping(contract.METRICS_PATH)
    backends = {
        str(target["runtime_backend"]) for target in contract._declared_targets().values()
    }
    for name, metric in document["metrics"].items():
        if metric["layer"] == "runtime":
            assert set(metric["backends"]) == backends, name


@pytest.mark.parametrize(
    "layer,metric_name",
    [("runtime", "runtime_queue_duration_seconds"), ("infrastructure", "gpu_memory_headroom_bytes")],
)
def test_unsupported_is_declared_rather_than_omitted(layer, metric_name):
    """측정할 수 없는 조합은 생략이 아니라 unsupported로 선언한다."""
    document = contract.load_yaml_mapping(contract.METRICS_PATH)
    metric = document["metrics"][metric_name]
    assert metric["layer"] == layer
    key = "backends" if layer == "runtime" else "targets"
    assert document["unsupported_marker"] in metric[key].values()


def test_names_follow_prometheus_base_unit_convention():
    """밀리초 이름은 저장소의 기존 지표·vLLM·OTel 어느 쪽과도 맞지 않는다."""
    document = contract.load_yaml_mapping(contract.METRICS_PATH)
    for name, metric in document["metrics"].items():
        assert "_ms" not in name, name
        suffix = contract._UNIT_SUFFIX.get(metric["unit"])
        if suffix:
            assert name.endswith(suffix), f"{name} declares {metric['unit']}"


def test_otel_mappings_reference_the_genai_semantic_conventions():
    document = contract.load_yaml_mapping(contract.METRICS_PATH)
    mapped = {
        name: metric["otel"]
        for name, metric in document["metrics"].items()
        if "otel" in metric
    }
    assert mapped, "표준 대응이 하나도 없으면 어휘를 새로 만든 것이다"
    for name, otel in mapped.items():
        assert otel.startswith(contract._OTEL_PREFIX), f"{name} -> {otel}"


def test_chunk_observing_layers_do_not_claim_token_granularity():
    """client는 소켓의 chunk 경계만 볼 수 있다(OTel GenAI semconv)."""
    document = contract.load_yaml_mapping(contract.METRICS_PATH)
    layers = document["layers"]
    for name, metric in document["metrics"].items():
        if layers[metric["layer"]].get("observes") == "chunks":
            assert "_to_first_token" not in name, name


def test_token_based_client_metric_declares_itself_an_approximation():
    document = contract.load_yaml_mapping(contract.METRICS_PATH)
    metric = document["metrics"]["client_time_per_output_token_seconds"]
    assert metric["approximation_of"] == "runtime_time_per_output_token_seconds"


def test_validate_covers_metrics_workloads_and_slo_together():
    """진입점이 하나여야 호출자가 일부만 검증하고 통과했다고 믿지 않는다."""
    failures: list[str] = []
    contract.validate(failures)
    assert failures == []


def test_every_workload_declares_a_cache_policy():
    """cache 상태를 기록하지 않으면 처리량이 과대 측정돼도 알 수 없다."""
    document = contract.load_yaml_mapping(contract.WORKLOADS_PATH)
    policies = set(document["cache_policies"])
    for name, workload in document["workloads"].items():
        assert workload["cache"]["policy"] in policies, name


def test_concurrency_sweeps_stay_within_the_declared_admission_limit():
    """Gateway admission을 넘는 sweep은 런타임이 아니라 Gateway 큐를 잰다."""
    document = contract.load_yaml_mapping(contract.WORKLOADS_PATH)
    for name, workload in document["workloads"].items():
        sweep = (workload.get("traffic") or {}).get("concurrency_sweep")
        if not sweep:
            continue
        limit = workload["traffic"]["admission_limit"]["max_concurrency"]
        assert max(sweep) <= limit, name


def test_slo_thresholds_are_not_set_without_a_declared_source():
    """baseline에서 유도한 값이 들어올 자리를 구조적으로 막는다."""
    document = contract.load_yaml_mapping(contract.SLO_PATH)
    sources = set(document["threshold_sources"])
    assert "baseline" not in sources
    for name, slo in document["slo_classes"].items():
        for metric_name, objective in slo["objectives"].items():
            extra = set(objective) - {"percentiles", "aggregate", "source"}
            if extra:
                assert objective.get("source") in sources, f"{name}.{metric_name}"


def test_result_schema_requires_the_environment_fingerprint():
    import json

    from jsonschema import Draft202012Validator

    from scripts.benchmark.contract import RESULT_SCHEMA_PATH

    schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert "environment" in schema["required"]
    fingerprint = schema["properties"]["environment"]["required"]
    for field in ("git_commit", "deployment_target", "model_revision", "runtime_flags"):
        assert field in fingerprint, field


def test_chunk_gap_metrics_are_not_judged_by_percentile():
    """전송 계층이 chunk를 묶어 보내면 간격의 percentile은 서버를 재지 않는다.

    macOS Metal target 실측에서 chunk 간격의 69%가 1ms 미만이고 p90이 65ms였다.
    같은 구간의 실제 토큰당 시간은 19.3ms다. p50도 p95도 그 값 근처에 없다.
    합계만 생성 구간과 일치했으므로 해석 가능한 통계를 계약이 선언하고, SLO가
    그 밖의 통계를 쓰지 못하게 막는다.
    """
    metrics = contract.load_yaml_mapping(contract.METRICS_PATH)["metrics"]
    gap_metrics = {
        name for name, metric in metrics.items()
        if metric.get("aggregation_unit") == "streamed_output_gap"
    }
    assert gap_metrics, "이 규칙이 지킬 대상이 사라지면 규칙도 의미가 없다"
    for name in gap_metrics:
        allowed = set(metrics[name].get("interpretable_statistics") or [])
        assert allowed, f"{name} must declare interpretable_statistics"
        assert "p95" not in allowed and "p50" not in allowed, name

    for slo_name, slo in contract.load_yaml_mapping(contract.SLO_PATH)["slo_classes"].items():
        for metric_name, objective in slo["objectives"].items():
            if metric_name in gap_metrics:
                assert "percentiles" not in objective, f"{slo_name}.{metric_name}"
                assert objective.get("aggregate") in (metrics[metric_name].get("interpretable_statistics") or [])


def test_every_primary_metric_has_a_producer_in_the_benchmark_client():
    """계약이 primary로 선언한 client 지표를 runner가 실제로 만들어야 한다.

    client_time_per_output_token_seconds는 계약에만 있고 만드는 곳이 없었다.
    그 상태로 Epic 5에 가면 판정 근거가 빈 채로 통과한다.
    """
    from scripts.benchmark.client import RequestSample

    document = contract.load_yaml_mapping(contract.WORKLOADS_PATH)
    metrics = contract.load_yaml_mapping(contract.METRICS_PATH)["metrics"]
    # 성공한 요청이 실제로 내보내는 문서만 본다. dataclass의 필드 목록을 함께 보면
    # 값이 문서에서 빠져도 테스트가 통과한다 -- 처음 쓴 판이 그래서 무력했다.
    sample = RequestSample(
        index=0,
        succeeded=True,
        status_code=200,
        client_time_to_first_chunk_seconds=0.5,
        client_operation_duration_seconds=2.0,
        client_time_per_output_chunk_seconds=[0.01, 0.01],
        client_input_tokens=512,
        client_output_tokens=64,
    )
    produced = set(sample.as_document())
    # 요청 단위로 관찰하는 지표만 확인한다. 비율·처리량은 evaluator가 집계한다.
    per_request = {
        name for name, metric in metrics.items()
        if metric["layer"] == "client" and metric.get("aggregation_unit") in ("request", "streamed_output_gap")
    }
    for workload in document["workloads"].values():
        for name in workload["primary_metrics"]:
            if name in per_request:
                assert name in produced, f"{name} is declared primary but nothing produces it"


def test_every_metric_declares_a_role_so_unused_ones_are_explainable():
    """소비처 없는 지표가 잊은 것인지 의도한 것인지 계약이 말해야 한다.

    27개 중 12개에 workload·SLO 소비처가 없었고, 그중 어느 것이 설계대로이고
    어느 것이 남은 것인지 가려내려면 매번 코드를 거슬러 읽어야 했다.
    """
    document = contract.load_yaml_mapping(contract.METRICS_PATH)
    roles = set(document["roles"])
    consumed = set()
    workloads = contract.load_yaml_mapping(contract.WORKLOADS_PATH)["workloads"]
    for workload in workloads.values():
        consumed |= set(workload.get("primary_metrics") or [])
        consumed |= set(workload.get("secondary_metrics") or [])
    for slo in contract.load_yaml_mapping(contract.SLO_PATH)["slo_classes"].values():
        consumed |= set(slo.get("objectives") or {})

    for name, metric in document["metrics"].items():
        assert metric.get("role") in roles, name
    # 판정용인데 아무 workload도 쓰지 않으면 그건 정말 잊힌 것이다.
    orphans = [
        name for name, metric in document["metrics"].items()
        if metric["role"] == "judgment" and name not in consumed
    ]
    assert orphans == [], orphans


def test_only_judgment_metrics_can_be_slo_objectives():
    """결과를 설명하려고 둔 값이 합격 여부를 정하게 두지 않는다."""
    metrics = contract.load_yaml_mapping(contract.METRICS_PATH)["metrics"]
    for name, slo in contract.load_yaml_mapping(contract.SLO_PATH)["slo_classes"].items():
        for metric_name in slo["objectives"]:
            assert metrics[metric_name]["role"] == "judgment", f"{name}.{metric_name}"


def test_judgment_metrics_declare_which_direction_is_better():
    """성공률 임계값이 '이 값보다 낮아야 한다'로 뒤집혀도 모르게 두지 않는다."""
    document = contract.load_yaml_mapping(contract.METRICS_PATH)
    directions = set(document["better_directions"])
    for name, metric in document["metrics"].items():
        if metric["role"] == "judgment":
            assert metric.get("better") in directions, name


def test_minimum_sample_counts_can_distinguish_the_percentile_they_gate():
    """p99를 20개 표본에서 내면 사실상 최댓값이고 판정이 표본 수에 달린다."""
    statistics = contract.load_yaml_mapping(contract.SLO_PATH)["statistics"]
    for name, share in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99)):
        assert statistics["minimum_samples"][name] >= round(1 / (1 - share)), name
