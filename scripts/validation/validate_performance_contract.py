#!/usr/bin/env python3
"""성능 지표 계약이 저장소의 다른 기준과 어긋나지 않는지 확인한다(ADR-0026).

계약이 이름을 단독 소유한다는 결정은, 그 이름이 실제로 존재하는 것을 가리킬 때만
의미가 있다. 이 검사는 하드웨어를 요구하지 않고 선언끼리만 대조한다. 런타임이
실제로 그 지표를 내는지는 runtime validation이 확인한다.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.configuration import load_yaml_mapping  # noqa: E402
from ai_model_serving.apps.mlx_metrics_exporter import render_mlx_metrics  # noqa: E402

METRICS_PATH = ROOT / "configs" / "performance" / "metrics.yaml"
TARGETS_PATH = ROOT / "configs" / "deployment_targets.yaml"
RULES_PATH = ROOT / "ops" / "prometheus" / "rules" / "model_runtime.rules.yml"
MONITORING_PATH = ROOT / "configs" / "monitoring.yaml"

_LAYER_KEY = {"runtime": "backends", "infrastructure": "targets"}
_REQUIRED_FIELDS = ("layer", "unit", "aggregation_unit", "definition")
# Prometheus naming convention: 이름은 base unit으로 끝나고 밀리초를 쓰지 않는다.
# 저장소의 기존 지표와 vLLM, OTel이 모두 초를 쓴다.
_UNIT_SUFFIX = {
    "seconds": "_seconds",
    "bytes": "_bytes",
    "ratio": "_ratio",
    "tokens_per_second": "_tokens_per_second",
    "requests_per_second": "_requests_per_second",
}
# 접미사로 판정할 수 없는 단위. 이름 규약 대신 값만 검사한다.
_UNIT_WITHOUT_SUFFIX = {"tokens", "count"}
_OTEL_PREFIX = "gen_ai."


def _recording_rules() -> set[str]:
    document = load_yaml_mapping(RULES_PATH)
    return {
        str(rule["record"])
        for group in document.get("groups", [])
        for rule in group.get("rules", [])
        if isinstance(rule, dict) and "record" in rule
    }


def _mlx_exporter_metric_names() -> set[str]:
    """MLX exporter가 낼 수 있는 이름 전체.

    이 exporter는 저장소가 소유하므로 upstream 이름을 추측하지 않고 코드에서
    직접 열거한다. 숫자가 들어간 payload를 주어야 sample이 생성되므로 모든
    source 키에 1을 채운 합성 payload를 쓴다.
    """
    filled: dict[str, Any] = {
        "summary": {
            key: 1
            for key in (
                "requests_started", "requests_completed", "requests_failed",
                "streaming_requests", "prompt_tokens_total", "completion_tokens_total",
                "generated_tokens_total", "uptime_s", "in_flight",
                "avg_request_time_s", "avg_request_tok_s", "avg_decode_tok_s",
            )
        },
        "server": {
            "loaded_model": "m", "request_queue_depth": 1, "effective_context_limit": 1,
        },
        "latest": {"prefill_tok_s": 1, "decode_tok_s": 1, "peak_memory_gb": 1},
    }
    rendered = render_mlx_metrics(filled, scrape_success=True)
    return {line.split()[2] for line in rendered.splitlines() if line.startswith("# TYPE")}


def _declared_vllm_metrics() -> set[str]:
    """configs/monitoring.yaml이 선언한 upstream vLLM 지표.

    vLLM 지표는 upstream이 소유하므로 정적으로 존재를 확인할 수 없다. 대신
    "우리가 의존한다고 선언한 목록"과 대조한다. 선언과 실제 런타임의 차이는
    runtime validation이 확인하므로, 둘을 합치면 오타와 upstream rename을
    모두 잡는다.
    """
    sources = load_yaml_mapping(MONITORING_PATH).get("metric_sources", {})
    declared = (sources.get("vllm_instances") or {}).get("required_metrics") or []
    return {str(name) for name in declared}


def _declared_targets() -> dict[str, dict[str, Any]]:
    document = load_yaml_mapping(TARGETS_PATH)
    targets = document.get("targets", document)
    return {name: value for name, value in targets.items() if isinstance(value, dict)}


def validate(failures: list[str]) -> None:
    contract = load_yaml_mapping(METRICS_PATH)
    if contract.get("version") != 1:
        failures.append("metrics.yaml must declare version 1")
        return

    layers = contract.get("layers", {})
    metrics = contract.get("metrics", {})
    if not isinstance(layers, dict) or not isinstance(metrics, dict) or not metrics:
        failures.append("metrics.yaml must define layers and metrics mappings")
        return

    targets = _declared_targets()
    target_ids = set(targets)
    backends = {str(value.get("runtime_backend")) for value in targets.values()}
    rules = _recording_rules()
    mlx_names = _mlx_exporter_metric_names()
    vllm_names = _declared_vllm_metrics()
    marker = str(contract.get("unsupported_marker", "unsupported"))

    for name, metric in metrics.items():
        if not isinstance(metric, dict):
            failures.append(f"metric {name!r} must be a mapping")
            continue
        for field in _REQUIRED_FIELDS:
            if not metric.get(field):
                failures.append(f"metric {name!r} is missing {field}")
        unit = str(metric.get("unit", ""))
        if unit not in _UNIT_SUFFIX and unit not in _UNIT_WITHOUT_SUFFIX:
            failures.append(f"metric {name!r} declares unknown unit {unit!r}")
        elif unit in _UNIT_SUFFIX and not name.endswith(_UNIT_SUFFIX[unit]):
            failures.append(
                f"metric {name!r} declares unit {unit!r} so the name must end with "
                f"{_UNIT_SUFFIX[unit]!r} (Prometheus base-unit convention)"
            )
        if "_ms" in name or name.endswith("_millis"):
            failures.append(f"metric {name!r} uses milliseconds; canonical names use base units")

        otel = metric.get("otel")
        if otel is not None and not str(otel).startswith(_OTEL_PREFIX):
            failures.append(
                f"metric {name!r} otel mapping {otel!r} must be an OpenTelemetry "
                f"GenAI semantic convention name starting with {_OTEL_PREFIX!r}"
            )

        approximates = metric.get("approximation_of")
        if approximates is not None and approximates not in metrics:
            failures.append(
                f"metric {name!r} approximates unknown metric {approximates!r}"
            )

        layer = str(metric.get("layer", ""))
        if layer not in layers:
            failures.append(f"metric {name!r} declares unknown layer {layer!r}")
            continue

        # client/gateway는 chunk를, runtime은 token을 관찰한다(OTel GenAI semconv).
        # client 층 이름이 token 경계를 관찰한 것처럼 보이면 근거 없는 값이 된다.
        observes = str(layers[layer].get("observes", ""))
        if observes == "chunks" and "_to_first_token" in name:
            failures.append(
                f"metric {name!r} is in a chunk-observing layer but claims token "
                "granularity; use time_to_first_chunk or move it to the runtime layer"
            )

        projection_key = _LAYER_KEY.get(layer)
        # client/gateway는 공유 코드가 만들므로 target별 projection을 갖지 않는다.
        # 그 층에 projection이 붙으면 target 무관이라는 결정이 무너진 것이다.
        for unexpected in set(_LAYER_KEY.values()) - {projection_key}:
            if unexpected in metric:
                failures.append(f"metric {name!r} in layer {layer!r} must not declare {unexpected}")
        if projection_key is None:
            continue

        projections = metric.get(projection_key)
        if not isinstance(projections, dict):
            failures.append(f"metric {name!r} must declare {projection_key}")
            continue
        expected = backends if projection_key == "backends" else target_ids
        if set(projections) != expected:
            missing = sorted(expected - set(projections))
            extra = sorted(set(projections) - expected)
            detail = ", ".join(filter(None, [
                f"missing {missing}" if missing else "",
                f"unknown {extra}" if extra else "",
            ]))
            failures.append(f"metric {name!r} {projection_key} mismatch: {detail}")
        for key, expression in projections.items():
            expression = str(expression)
            if expression == marker:
                continue
            if expression in rules:
                continue
            # 백엔드마다 "이 이름이 실재하는가"를 확인할 수 있는 근거가 다르다.
            # MLX exporter는 저장소가 소유하므로 코드에서 열거해 대조한다. vLLM은
            # upstream이 소유하므로 정적으로 확인할 수 없고, 이름 규약만 검사한 뒤
            # 실제 존재 여부는 runtime validation이 확인한다.
            if key == "mlx-vlm":
                if expression not in mlx_names:
                    failures.append(
                        f"metric {name!r} {projection_key}.{key} points at {expression!r} "
                        "which the MLX exporter never emits"
                    )
            elif key == "vllm-cuda":
                if expression not in vllm_names:
                    failures.append(
                        f"metric {name!r} {projection_key}.{key} points at {expression!r} "
                        "which configs/monitoring.yaml does not declare as a required vLLM metric"
                    )
            elif not expression.startswith(("vllm:", "mlx_runtime_")):
                failures.append(
                    f"metric {name!r} {projection_key}.{key} points at {expression!r} "
                    "which is not a recording rule and has no known source"
                )

    for name, derived in (contract.get("derived") or {}).items():
        if not isinstance(derived, dict):
            failures.append(f"derived {name!r} must be a mapping")
            continue
        for source in derived.get("from", []):
            if source not in metrics:
                failures.append(f"derived {name!r} references unknown metric {source!r}")


def main() -> int:
    failures: list[str] = []
    try:
        validate(failures)
    except (OSError, RuntimeError, yaml.YAMLError) as exc:
        print(f"[performance] cannot validate contract: {exc}", file=sys.stderr)
        return 2
    if failures:
        for failure in failures:
            print(f"[performance] fail: {failure}", file=sys.stderr)
        return 1
    print("[performance] ok: metric contract is consistent with targets and recording rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
