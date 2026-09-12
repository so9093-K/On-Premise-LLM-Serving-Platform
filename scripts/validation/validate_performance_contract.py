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
# scripts/ 아래 모듈을 import하려면 저장소 루트도 필요하다.
for _entry in (str(ROOT), str(ROOT / "src")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from ai_model_serving.configuration import load_yaml_mapping  # noqa: E402
from ai_model_serving.apps.mlx_metrics_exporter import render_mlx_metrics  # noqa: E402

# 계약 파일의 위치는 scripts/benchmark/contract.py가 소유한다. 여기서 다시
# 계산하면 검증기와 실행기가 서로 다른 파일을 볼 수 있다.
from scripts.benchmark.contract import (  # noqa: E402
    METRICS_PATH,
    RESULT_SCHEMA_PATH,
    SLO_PATH,
    SWEEP_SCHEMA_PATH,
    WORKLOADS_PATH,
)
MODEL_SERVING_PATH = ROOT / "configs" / "model_serving.yaml"
MACOS_RUNTIME_PATH = ROOT / "configs" / "macos_mlx_runtime.yaml"
MAIN_PROFILES_PATH = ROOT / "configs" / "main_model_profiles.yaml"
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


def _validate_metrics(failures: list[str]) -> dict[str, Any]:
    contract = load_yaml_mapping(METRICS_PATH)
    if contract.get("version") != 1:
        failures.append("metrics.yaml must declare version 1")
        return {}

    layers = contract.get("layers", {})
    metrics = contract.get("metrics", {})
    if not isinstance(layers, dict) or not isinstance(metrics, dict) or not metrics:
        failures.append("metrics.yaml must define layers and metrics mappings")
        return {}

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

    _validate_roles(failures, contract, metrics)
    return metrics


def _validate_roles(failures: list[str], contract: dict[str, Any], metrics: dict[str, Any]) -> None:
    """역할 선언이 주석에 그치지 않게 한다.

    소비처 없는 지표를 두고 "잊은 것"인지 "의도한 것"인지 구분할 수 없으면, 계약을
    읽는 사람이 매번 다시 따져야 한다. 실제로 27개 중 12개가 그 상태였다.
    """
    roles = set(contract.get("roles") or {})
    directions = set(contract.get("better_directions") or {"lower", "higher"})
    if not roles:
        failures.append("metrics.yaml must declare roles")
        return
    for name, metric in metrics.items():
        role = metric.get("role")
        if role not in roles:
            failures.append(f"metric {name!r} declares unknown role {role!r}")
            continue
        # 판정 방향이 없으면 성공률 임계값이 "이 값보다 낮아야 한다"로 뒤집혀도
        # 아무도 모른다.
        if role == "judgment" and metric.get("better") not in directions:
            failures.append(f"judgment metric {name!r} must declare better: {sorted(directions)}")
        # gateway 층은 계약 스스로 SLO 기준이 아니라고 선언했다.
        if role == "judgment" and metric["layer"] == "gateway":
            failures.append(
                f"metric {name!r} is judgment but the gateway layer declares itself a "
                "diagnosis basis, not an SLO basis"
            )
        target = metric.get("approximation_of")
        if target and metrics.get(target, {}).get("role") != "reference":
            failures.append(
                f"metric {name!r} approximates {target!r} which must declare role 'reference'"
            )
        # 전송 계층이 chunk를 묶어 보내면 도착 간격의 percentile은 서버가 아니라
        # 그 묶임을 잰다. macOS Metal 실측에서 간격의 69%가 1ms 미만이고 p90이
        # 65ms였는데, 같은 구간의 실제 토큰당 시간은 19.3ms였다.
        if metric.get("aggregation_unit") == "streamed_output_gap":
            allowed = set(metric.get("interpretable_statistics") or [])
            if not allowed:
                failures.append(f"metric {name!r} must declare interpretable_statistics")
            elif allowed & {"p50", "p95", "p99"}:
                failures.append(
                    f"metric {name!r} measures chunk arrival gaps; percentiles of that series "
                    "describe the transport, not the server"
                )


def _supported_request_parameters() -> dict[str, set[str]]:
    """profile별로 Gateway가 통과시키는 요청 파라미터.

    이 Gateway는 allow_unlisted_parameters=false라 선언되지 않은 필드를 422로
    막는다. workload가 stream_options를 요구하는데 profile이 허용하지 않으면
    응답에 usage가 실리지 않아 토큰 기준 값이 조용히 빈다. 실제로 macOS profile이
    그 상태였다.
    """
    supported: dict[str, set[str]] = {}
    serving = load_yaml_mapping(MODEL_SERVING_PATH)
    for key, model in (serving.get("models") or {}).items():
        policy = (model.get("gateway_policy") or {}).get("request_parameter_policy") or {}
        names = policy.get("supported_parameters")
        if names:
            supported[f"model_serving:{key}"] = {str(n) for n in names}
    macos = load_yaml_mapping(MACOS_RUNTIME_PATH)
    for key, profile in (macos.get("profiles") or {}).items():
        policy = (profile.get("gateway_policy") or {}).get("request_parameter_policy") or {}
        names = policy.get("supported_parameters")
        if names:
            supported[f"macos:{key}"] = {str(n) for n in names}
    return supported


def _validate_workloads(failures: list[str], metrics: dict[str, Any]) -> dict[str, Any]:
    document = load_yaml_mapping(WORKLOADS_PATH)
    _validate_capacity_criterion(failures, document)
    _validate_monitoring_stack_declaration(failures)
    if document.get("version") != 1:
        failures.append("workloads.yaml must declare version 1")
        return {}
    cache_policies = set(document.get("cache_policies") or {})
    traffic_modes = set(document.get("traffic_modes") or {})
    workloads = document.get("workloads") or {}
    supported = _supported_request_parameters()

    for name, workload in workloads.items():
        if not isinstance(workload, dict):
            failures.append(f"workload {name!r} must be a mapping")
            continue

        cache = workload.get("cache") or {}
        policy = str(cache.get("policy", ""))
        if policy not in cache_policies:
            failures.append(f"workload {name!r} declares unknown cache policy {policy!r}")

        traffic = workload.get("traffic") or {}
        mode = str(traffic.get("mode", ""))
        if mode not in traffic_modes:
            failures.append(f"workload {name!r} declares unknown traffic mode {mode!r}")
        # ADR 7절: admission 한도를 넘는 sweep은 런타임이 아니라 Gateway 큐를 잰다.
        sweep = traffic.get("concurrency_sweep")
        if sweep:
            limit = traffic.get("admission_limit") or {}
            declared = limit.get("max_concurrency")
            if not declared:
                failures.append(
                    f"workload {name!r} sweeps concurrency but declares no admission_limit; "
                    "the result would measure the Gateway queue, not the runtime"
                )
            elif max(sweep) > int(declared):
                failures.append(
                    f"workload {name!r} sweeps up to {max(sweep)} beyond the declared "
                    f"admission limit {declared}"
                )

        required_params = set(workload.get("required_request_parameters") or [])
        if required_params:
            unusable = sorted(
                profile for profile, names in supported.items()
                if not required_params.issubset(names)
            )
            if unusable:
                failures.append(
                    f"workload {name!r} requires {sorted(required_params)} which these "
                    f"profiles do not accept: {unusable}"
                )

        # 코드가 기본값을 들고 있으면 계약에서 값을 지워도 조용히 돈다. 512 토큰을
        # 선언했다 지우면 아무도 모르게 512로 돌아가고, 결과는 계약이 말하는 것과
        # 다른 조건에서 나온다. 실행에 필요한 값은 계약이 반드시 갖는다.
        for section, key in (("prompt", "input_tokens"), ("output", "max_tokens"),
                             ("duration", "measurement_seconds"), ("duration", "warmup_seconds")):
            block = workload.get(section) or {}
            if key == "input_tokens" and block.get("input_tokens_sweep"):
                continue  # 길이 sweep은 목록이 그 자리를 대신한다
            if key not in block:
                failures.append(f"workload {name!r} must declare {section}.{key}")

        _validate_context_requirements(failures, name, workload)
        _validate_cache_requirements(failures, name, workload)

        # 응답 usage가 있어야 계산되는 지표를 쓰면서 stream_options를 요청하지 않으면
        # 그 지표가 조용히 빈다. 지금까지 metrics.yaml의 requires.response_usage를
        # 아무도 읽지 않아, 두 선언이 서로를 모른 채 나란히 있었다.
        used = set(workload.get("primary_metrics") or []) | set(workload.get("secondary_metrics") or [])
        needs_usage = sorted(
            metric_name for metric_name in used
            if (metrics.get(metric_name, {}).get("requires") or {}).get("response_usage")
        )
        if needs_usage and "stream_options" not in (workload.get("required_request_parameters") or []):
            failures.append(
                f"workload {name!r} uses {needs_usage} which need response usage, but does not "
                "require stream_options; 요청하지 않으면 그 지표가 조용히 빈다"
            )

        # 두 축을 동시에 훑으면 2차원 sweep이 된다. 구현하지 않으므로, 선언만
        # 해두면 한쪽 축은 조용히 첫 값으로 고정된 채 돈다.
        multi = [
            axis for axis, values in (
                ("request_rate_sweep", (workload.get("traffic") or {}).get("request_rate_sweep")),
                ("concurrency_sweep", (workload.get("traffic") or {}).get("concurrency_sweep")),
                ("input_tokens_sweep", (workload.get("prompt") or {}).get("input_tokens_sweep")),
            ) if values and len(values) > 1
        ]
        if len(multi) > 1:
            failures.append(
                f"workload {name!r} declares more than one multi-point sweep ({multi}); "
                "2차원 sweep은 구현하지 않으며, 한 축은 조용히 첫 값으로 고정된다"
            )

        for field in ("primary_metrics", "secondary_metrics"):
            for metric_name in workload.get(field) or []:
                if metric_name not in metrics:
                    failures.append(
                        f"workload {name!r} {field} references unknown metric {metric_name!r}"
                    )
        if not workload.get("primary_metrics"):
            failures.append(f"workload {name!r} must declare primary_metrics")
        # primary는 판정 기준이므로 어느 target에서도 측정 불가한 지표를 쓰면 안 된다.
        for metric_name in workload.get("primary_metrics") or []:
            metric = metrics.get(metric_name) or {}
            if metric.get("layer") in ("runtime", "infrastructure"):
                failures.append(
                    f"workload {name!r} uses {metric_name!r} as a primary metric, but that "
                    "layer is unsupported on some targets; keep it secondary"
                )
    return workloads


def _declared_context_limits() -> dict[str, int]:
    """profile별로 선언된 context 상한. 실행 명령과 gateway 정책 둘 다 본다."""
    document = load_yaml_mapping(MAIN_PROFILES_PATH)
    limits: dict[str, int] = {}
    for name, profile in (document.get("profiles") or {}).items():
        limit = ((profile.get("gateway_policy") or {}).get("request_limits") or {}).get("max_model_len")
        if limit is None:
            command = profile.get("command") or []
            if "--max-model-len" in command:
                limit = command[command.index("--max-model-len") + 1]
        if limit is not None:
            limits[str(name)] = int(limit)
    return limits


def _validate_context_requirements(failures: list[str], name: str, workload: dict[str, Any]) -> None:
    """workload가 요구하는 context를 담지 못하는 profile을 선언과 대조한다.

    계약이 24,704 토큰을 요구하는데 gemma4-26b-a4b-fp8은 20,000이다. 선언하지
    않으면 그 profile에서 몇 분을 측정한 뒤 요청이 거부되는 것으로 알게 된다.
    지표의 unsupported와 같은 이유로, 생략이 아니라 선언으로 남긴다.
    """
    prompt = workload.get("prompt") or {}
    lengths = prompt.get("input_tokens_sweep") or [prompt.get("input_tokens")]
    lengths = [int(value) for value in lengths if value]
    if not lengths:
        return
    needed = max(lengths) + int((workload.get("output") or {}).get("max_tokens", 0))
    too_small = {
        profile for profile, limit in _declared_context_limits().items() if limit < needed
    }
    declared = set(workload.get("unsupported_profiles") or {})
    for profile in sorted(too_small - declared):
        failures.append(
            f"workload {name!r} needs {needed} context tokens but profile {profile!r} declares "
            "less; unsupported_profiles에 이유와 함께 선언한다"
        )
    for profile in sorted(declared - too_small):
        failures.append(
            f"workload {name!r} declares profile {profile!r} unsupported, but its context limit "
            f"covers {needed} tokens; 선언이 실제와 어긋난다"
        )


def _backends_with_prefix_caching() -> set[str]:
    """런타임 설정이 prefix 캐시를 켜는 backend.

    vLLM은 profile 실행 명령줄에서, MLX는 런타임 설정에서 본다. 어느 쪽도 선언이
    없으면 그 backend는 이 기능을 제공하지 않는다.
    """
    enabled: set[str] = set()
    profiles = (load_yaml_mapping(MAIN_PROFILES_PATH).get("profiles") or {}).values()
    if any("--enable-prefix-caching" in [str(item) for item in (p.get("command") or [])] for p in profiles):
        enabled.add("vllm-cuda")
    macos = load_yaml_mapping(MACOS_RUNTIME_PATH).get("runtime") or {}
    if macos.get("prefix_caching") or macos.get("enable_prefix_caching"):
        enabled.add("mlx-vlm")
    return enabled


def _validate_cache_requirements(failures: list[str], name: str, workload: dict[str, Any]) -> None:
    """cache 재사용을 묻는 workload가 그것을 제공하는 backend를 정확히 가리키는지 본다.

    제공하지 않는 backend에서도 요청은 성공하므로 숫자가 나온다. 그 숫자를 cache
    효과로 읽으면 틀린다. 선언이 실제와 어긋나면 그 오독을 막지 못한다.
    """
    if not (workload.get("cache") or {}).get("requires_prefix_caching"):
        return
    declared_backends = {
        str(target["runtime_backend"]) for target in _declared_targets().values()
    }
    without = declared_backends - _backends_with_prefix_caching()
    declared = set(workload.get("unsupported_backends") or {})
    for backend in sorted(without - declared):
        failures.append(
            f"workload {name!r} needs prefix caching but backend {backend!r} does not enable it; "
            "unsupported_backends에 이유와 함께 선언한다"
        )
    for backend in sorted(declared - without):
        failures.append(
            f"workload {name!r} declares backend {backend!r} unsupported, but its runtime enables "
            "prefix caching; 선언이 실제와 어긋난다"
        )


def _validate_monitoring_stack_declaration(failures: list[str]) -> None:
    """target의 runs_monitoring_stack 선언이 실제 compose 구성과 맞는지 본다.

    사실은 compose가 갖고 있지만 수집기가 compose를 파싱하게 만들지 않는다.
    선언하고 여기서 대조한다. 어긋나면 수집기가 없는 Prometheus를 찌르거나,
    있는 것을 건너뛴다.
    """
    targets = _declared_targets()
    for name, target in targets.items():
        files = target.get("compose_files") or ["ops/compose/full-stack.private-network.yaml"]
        actual = any(
            (ROOT / path).exists() and "\n  prometheus:" in (ROOT / path).read_text(encoding="utf-8")
            for path in files
        )
        declared = bool(target.get("runs_monitoring_stack", True))
        if declared != actual:
            failures.append(
                f"deployment target {name!r} declares runs_monitoring_stack={declared} but its "
                f"compose files {'include' if actual else 'do not include'} prometheus"
            )


def _validate_capacity_criterion(failures: list[str], document: dict[str, Any]) -> None:
    """sweep이 쓰는 규칙이 전부 선언됐는지 확인한다.

    빠지면 sweep이 몇 분을 측정한 뒤 KeyError로 터진다. 그 시점에는 이미 스택을
    띄우고 수십 건을 보낸 뒤다.
    """
    from scripts.benchmark.sweep import required_criterion_fields

    criterion = document.get("capacity_criterion") or {}
    if not criterion:
        failures.append("workloads.yaml must declare capacity_criterion")
        return
    for field in required_criterion_fields():
        if field not in criterion:
            failures.append(f"capacity_criterion must declare {field!r}")


def _validate_slo(failures: list[str], metrics: dict[str, Any], workloads: dict[str, Any]) -> None:
    document = load_yaml_mapping(SLO_PATH)
    if document.get("version") != 1:
        failures.append("slo.yaml must declare version 1")
        return
    statistics = document.get("statistics") or {}
    if not statistics.get("percentile_method"):
        failures.append("slo.yaml must declare statistics.percentile_method")
    minimum = statistics.get("minimum_samples") or {}
    for percentile_name, share in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99)):
        # p번째 백분위를 구분하려면 최소 1/(1-p)개의 표본이 필요하다. 그보다 적게
        # 잡으면 사실상 최댓값을 임계값과 비교하게 된다.
        needed = round(1 / (1 - share))
        if int(minimum.get(percentile_name, 0)) < needed:
            failures.append(
                f"slo.yaml minimum_samples.{percentile_name} must be at least {needed}"
            )
    levels = set(document.get("enforcement_levels") or {})
    sources = set(document.get("threshold_sources") or {})
    if "baseline" in sources:
        failures.append("slo.yaml must not allow baseline as a threshold source")

    for name, slo in (document.get("slo_classes") or {}).items():
        if not isinstance(slo, dict):
            failures.append(f"slo class {name!r} must be a mapping")
            continue
        if str(slo.get("enforcement", "")) not in levels:
            failures.append(f"slo class {name!r} declares unknown enforcement level")
        workload = str(slo.get("workload", ""))
        if workload not in workloads:
            failures.append(f"slo class {name!r} references unknown workload {workload!r}")
        objectives = slo.get("objectives") or {}
        if not objectives:
            failures.append(f"slo class {name!r} must declare objectives")
        for metric_name, objective in objectives.items():
            if metric_name not in metrics:
                failures.append(
                    f"slo class {name!r} references unknown metric {metric_name!r}"
                )
            metric = metrics.get(metric_name) or {}
            allowed = set(metric.get("interpretable_statistics") or [])
            if allowed and set(objective.get("percentiles") or []) - allowed:
                failures.append(
                    f"slo class {name!r} judges {metric_name!r} by percentile, but the contract "
                    f"declares only {sorted(allowed)} interpretable for it"
                )
            # 판정은 judgment 역할에서만 고른다. diagnosis를 기준으로 삼으면
            # 결과를 설명하려고 둔 값이 합격 여부를 정하게 된다.
            elif metrics[metric_name].get("role") != "judgment":
                failures.append(
                    f"slo class {name!r} judges {metric_name!r} which declares role "
                    f"{metrics[metric_name].get('role')!r}; objectives must use judgment metrics"
                )
            # 임계값이 채워질 때는 출처를 함께 적어야 한다. 출처 없는 숫자는
            # baseline에서 유도된 값과 구분할 수 없다.
            # 측정으로 그은 선은 정의상 어떤 구성에서 나온 것이다. 단일 threshold로
            # 적으면 그 구성이 어디에도 남지 않고 모든 실행에 적용된다. 실제로
            # 부하 의존 지표에 이 형태를 쓰면 부하 명시 요구를 그대로 우회했다.
            if str(objective.get("source", "")) == "regression_guard" and "threshold" in objective:
                failures.append(
                    f"slo class {name!r} objective {metric_name!r} is a regression_guard with a "
                    "single threshold; 측정으로 그은 선은 thresholds_by_configuration으로 "
                    "어떤 구성에서 잰 것인지 함께 적는다"
                )
            _validate_configuration_thresholds(failures, name, metric_name, objective, metrics)
            has_threshold = any(
                key not in ("percentiles", "aggregate", "source") for key in objective
            )
            if has_threshold and str(objective.get("source", "")) not in sources:
                failures.append(
                    f"slo class {name!r} objective {metric_name!r} sets a threshold "
                    f"without a declared source; allowed: {sorted(sources)}"
                )


def _validate_configuration_thresholds(
    failures: list[str],
    slo_name: str,
    metric_name: str,
    objective: dict[str, Any],
    metrics: dict[str, Any],
) -> None:
    """측정으로 그은 선이 어떤 구성에서 나왔는지 정확히 가리키는지 확인한다.

    match가 비어 있거나 모르는 항목만 있으면 그 선은 모든 구성에 걸린다. 다른
    모델·다른 가속기의 결과를 그 숫자로 판정하게 되므로 근거 없는 합격이 나온다.
    """
    from scripts.benchmark.evaluate import MATCHABLE_FIELDS

    where = f"slo class {slo_name!r} objective {metric_name!r}"
    load_dependent = bool(metrics.get(metric_name, {}).get("load_dependent"))
    entries = objective.get("thresholds_by_configuration")
    if entries is None:
        return
    if not isinstance(entries, list) or not entries:
        failures.append(f"{where} thresholds_by_configuration must be a non-empty list")
        return
    targets = _declared_targets()
    for index, entry in enumerate(entries):
        match = (entry or {}).get("match") or {}
        if not match:
            failures.append(f"{where} entry {index} must declare a non-empty match")
        if "threshold" not in (entry or {}):
            failures.append(f"{where} entry {index} must declare a threshold")
        for field, value in match.items():
            if field not in MATCHABLE_FIELDS:
                failures.append(
                    f"{where} entry {index} matches on unknown field {field!r}; "
                    f"allowed: {sorted(MATCHABLE_FIELDS)}"
                )
            elif field == "deployment_target" and value not in targets:
                failures.append(
                    f"{where} entry {index} matches unknown deployment target {value!r}"
                )
        # 무엇을 재고 그은 선인지 적히지 않으면 6개월 뒤 아무도 이 숫자를 바꾸지 못한다.
        if str(objective.get("source", "")) == "regression_guard":
            if not (entry or {}).get("measured"):
                failures.append(f"{where} entry {index} is a regression_guard without a measured note")
            # 부하가 값을 바꾸는 지표는 어느 부하에서 쟀는지까지 적어야 한다. 안 적으면
            # 다른 부하의 실행에 그 선이 적용되어 대기열 길이를 모델 성능으로 판정한다.
            if load_dependent and "request_rate_per_second" not in match:
                failures.append(
                    f"{where} entry {index} guards a load_dependent metric but does not pin "
                    "request_rate_per_second; 다른 부하의 실행에 이 선이 적용된다"
                )


def _validate_result_schema(failures: list[str]) -> None:
    """결과 schema도 계약의 일부다.

    지문 없이 저장된 결과는 6개월 뒤 해석할 수 없다. required에서 빠지면 그때부터
    조용히 지문 없는 결과가 쌓인다.
    """
    import json

    from jsonschema import Draft202012Validator

    try:
        schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read {RESULT_SCHEMA_PATH.name}: {exc}")
        return
    Draft202012Validator.check_schema(schema)
    # 용량 요약도 계약의 일부다. 이 문서가 SLO 기준선이 "어느 부하에서 잰 값"인지
    # 가리키는 근거이므로, 구성과 판정 규칙이 required에서 빠지면 근거가 사라진다.
    try:
        sweep_schema = json.loads(SWEEP_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read {SWEEP_SCHEMA_PATH.name}: {exc}")
    else:
        Draft202012Validator.check_schema(sweep_schema)
        for field in ("environment", "criterion", "axis", "sustained_point"):
            if field not in sweep_schema.get("required", []):
                failures.append(f"performance_sweep schema must require {field!r}")
    if "environment" not in schema.get("required", []):
        failures.append("performance_run schema must require the environment fingerprint")
        return
    fingerprint = set(schema["properties"]["environment"].get("required", []))
    for field in ("git_commit", "deployment_target", "model_revision", "runtime_flags"):
        if field not in fingerprint:
            failures.append(f"performance_run schema environment must require {field!r}")


def _validate_regression_policy(failures: list[str], metrics: dict[str, Any]) -> None:
    """회귀 비교 정책이 계약의 이름과 역할을 벗어나지 않는지 본다.

    diagnosis 지표가 변했다는 사실만으로는 회귀라고 말할 수 없다. 그것은 결과를
    설명하는 값이지 판정 대상이 아니다.
    """
    from scripts.benchmark.baseline import BASELINE_SCHEMA_PATH, REGRESSION_PATH

    document = load_yaml_mapping(REGRESSION_PATH)
    if document.get("version") != 1:
        failures.append("regression.yaml must declare version 1")
        return
    from scripts.benchmark.baseline import REQUIRED_PROMOTION_RULES

    promotion = document.get("promotion") or {}
    for key in REQUIRED_PROMOTION_RULES:
        if key not in promotion:
            failures.append(f"regression.yaml promotion must declare {key!r}")
    tolerance = (document.get("tolerance") or {}).get("default_ratio")
    if not isinstance(tolerance, (int, float)) or not 0 < float(tolerance) < 1:
        failures.append("regression.yaml must declare a tolerance.default_ratio between 0 and 1")
    statistics = load_yaml_mapping(SLO_PATH).get("statistics") or {}
    known = set(statistics.get("minimum_samples") or {}) | {"value", "mean", "min", "max"}
    for name, wanted in (document.get("compared") or {}).items():
        metric = metrics.get(name)
        if metric is None:
            failures.append(f"regression.yaml compares unknown metric {name!r}")
            continue
        if metric.get("role") != "judgment":
            failures.append(
                f"regression.yaml compares {name!r} which declares role {metric.get('role')!r}; "
                "diagnosis가 변했다는 사실만으로는 회귀라고 말할 수 없다"
            )
        for statistic in wanted or []:
            if statistic not in known:
                failures.append(f"regression.yaml compares {name!r} by unknown statistic {statistic!r}")
    import json as _json

    from jsonschema import Draft202012Validator

    try:
        Draft202012Validator.check_schema(
            _json.loads(BASELINE_SCHEMA_PATH.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as exc:
        failures.append(f"cannot read {BASELINE_SCHEMA_PATH.name}: {exc}")


def _validate_promoted_baselines(failures: list[str], metrics: dict[str, Any]) -> None:
    """커밋된 baseline이 schema와 계약을 지키는지 본다.

    이 파일들은 저장소가 소유하고 리뷰를 거친다. 그런데 검증을 받지 않으면 깨진
    파일이 들어와도 릴리스 게이트를 돌릴 때에야 드러난다. 실제로 required 필드를
    지운 파일이 그대로 로드됐다.
    """
    import json as _json

    from jsonschema import Draft202012Validator

    from scripts.benchmark.baseline import BASELINE_DIR, BASELINE_SCHEMA_PATH

    if not BASELINE_DIR.is_dir():
        return
    schema = _json.loads(BASELINE_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for path in sorted(BASELINE_DIR.glob("*.json")):
        try:
            document = _json.loads(path.read_text(encoding="utf-8"))
        except _json.JSONDecodeError as exc:
            failures.append(f"baseline {path.name} is not valid JSON: {exc}")
            continue
        for error in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path)):
            location = "/".join(str(part) for part in error.absolute_path) or "<root>"
            failures.append(f"baseline {path.name} {location}: {error.message}")
        # 계약이 모르는 이름으로 굳힌 baseline은 어떤 실행과도 견줄 수 없다.
        for metric_name in document.get("statistics") or {}:
            if metric_name not in metrics:
                failures.append(f"baseline {path.name} records unknown metric {metric_name!r}")


def validate(failures: list[str]) -> None:
    """계약 세 파일을 한 번에 검사한다.

    metric만 보는 함수를 validate로 노출했더니, 호출자가 전체를 검증했다고
    믿으면서 workload와 SLO를 건너뛸 수 있었다. 진입점을 하나로 둔다.
    """
    metrics = _validate_metrics(failures)
    workloads = _validate_workloads(failures, metrics)
    _validate_slo(failures, metrics, workloads)
    _validate_result_schema(failures)
    _validate_regression_policy(failures, metrics)
    _validate_promoted_baselines(failures, metrics)


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
    print("[performance] ok: metric, workload, and SLO contracts are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
