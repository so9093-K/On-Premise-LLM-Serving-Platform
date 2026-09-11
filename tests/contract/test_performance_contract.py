"""성능 지표 계약이 저장소의 다른 기준과 갈라지지 않는지 고정한다(ADR-0026).

이 계약은 metric 이름을 단독 소유한다. 이름이 실재하지 않는 것을 가리키면
benchmark와 dashboard가 조용히 빈 값을 받으므로, 참조 무결성을 테스트로 고정한다.
"""

from __future__ import annotations

import pytest

from scripts.validation import validate_performance_contract as contract


def test_metric_contract_matches_targets_rules_and_exporters():
    failures: list[str] = []
    contract.validate(failures)
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
