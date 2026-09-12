"""baseline 승격과 회귀 판정(ADR-0026 1·9절).

baseline은 SLO가 아니다. SLO는 제공하려는 품질이고 baseline은 특정 구성에서 나온
관찰이다. 그래서 SLO 임계값이 비어 있어도 회귀 판정은 동작한다 -- 비교 대상이
약속이 아니라 이전 관찰이기 때문이다.

벤치마크 실행이 baseline을 덮어쓰지 않는다. 자동 갱신되면 성능이 서서히 나빠져도
baseline이 따라 내려가 회귀를 영원히 못 잡는다. 승격은 사람이 한다.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.benchmark.contract import PERFORMANCE_DIR, ROOT, PerformanceContract, load_yaml_mapping

BASELINE_DIR = ROOT / "benchmarks" / "baselines"
BASELINE_SCHEMA_PATH = ROOT / "specs" / "schemas" / "performance_baseline.schema.json"
REGRESSION_PATH = PERFORMANCE_DIR / "regression.yaml"

# baseline이 유효한 범위를 정하는 항목. 값을 정하는 것은 모델과 가속기와 런타임이며
# target 이름이 아니다. thresholds_by_configuration의 match와 같은 생각이다.
_ENVIRONMENT_KEYS = (
    "runtime_backend", "deployment_target", "runtime_profile", "model_id", "model_revision",
)
_WORKLOAD_KEYS = ("request_rate_per_second", "concurrency", "input_tokens")


class PromotionRefused(RuntimeError):
    """이 실행은 baseline이 될 수 없다."""


def policy() -> dict[str, Any]:
    return load_yaml_mapping(REGRESSION_PATH)


def configuration_of(document: dict[str, Any]) -> dict[str, Any]:
    """이 실행이 어떤 구성이었는가."""
    environment = document.get("environment") or {}
    workload = document.get("workload") or {}
    traffic = workload.get("traffic") or {}
    configuration: dict[str, Any] = {
        key: environment[key] for key in _ENVIRONMENT_KEYS if key in environment
    }
    gpu = environment.get("gpu") or {}
    if gpu:
        configuration["gpu_model"] = gpu.get("model")
        configuration["gpu_memory_kind"] = gpu.get("memory_kind")
    configuration["workload_id"] = workload["id"]
    if "input_tokens" in workload:
        configuration["input_tokens"] = int(workload["input_tokens"])
    for key in _WORKLOAD_KEYS:
        if key in traffic:
            configuration[key] = traffic[key]
    return configuration


def slug(configuration: dict[str, Any]) -> str:
    """사람이 읽을 수 있는 파일 이름. 리뷰 대상이므로 해시를 쓰지 않는다."""
    parts = [
        str(configuration.get("workload_id", "unknown")),
        str(configuration.get("runtime_profile", configuration.get("runtime_backend", "unknown"))),
    ]
    for key in _WORKLOAD_KEYS:
        if key in configuration:
            value = configuration[key]
            parts.append(f"{key.split('_')[0]}{value:g}" if isinstance(value, (int, float)) else str(value))
    return "-".join(part.replace("/", "_").replace(" ", "_") for part in parts)


def _assert_promotable(
    document: dict[str, Any], contract: PerformanceContract, rules: dict[str, Any]
) -> None:
    run = document["run"]
    summary = document.get("summary") or {}
    success = (summary.get("client_request_success_ratio") or {}).get("value")
    required = float(rules.get("require_success_ratio", 1.0))
    if success is None or success < required:
        raise PromotionRefused(
            f"success ratio {success} is below {required}; 버린 요청이 있으면 지연 분포는 "
            "살아남은 요청의 것이고, 이후 정상 실행이 개선으로 보인다"
        )
    limit = float(rules.get("max_dispatch_lag_seconds", 0.5))
    lag = float(run.get("max_dispatch_lag_seconds", 0.0))
    if lag > limit:
        raise PromotionRefused(
            f"dispatch lag {lag:.3f}s exceeds {limit:g}s; 도구가 목표 시각을 못 지킨 실행은 "
            "서버가 아니라 도구를 잰 것이다"
        )
    if rules.get("require_load_inside_capacity", True):
        _assert_load_inside_capacity(document, contract)
    if rules.get("require_declared_sweep_point", True):
        _assert_declared_sweep_point(document, contract)


def _assert_load_inside_capacity(document: dict[str, Any], contract: PerformanceContract) -> None:
    """부하가 용량 안이었는지 본다.

    sweep의 판정 신호를 그대로 쓴다. 측정 구간 뒤쪽 절반의 첫 응답이 앞쪽보다 크게
    늘었으면 대기가 쌓인 것이고, 그 값을 baseline으로 굳히면 큐 길이가 기준이 된다.
    """
    from scripts.benchmark.sweep import drift_ratio

    criterion = contract.capacity_criterion
    ratio = drift_ratio(document["requests"])
    limit = float(criterion["max_second_half_ratio"])
    if ratio is not None and ratio > limit:
        raise PromotionRefused(
            f"first-chunk latency drifted {ratio:.2f}x within the run (limit {limit:g}); "
            "용량 밖에서 잰 값을 baseline으로 굳히면 큐 길이가 기준이 된다"
        )


def _assert_declared_sweep_point(document: dict[str, Any], contract: PerformanceContract) -> None:
    """계약이 선언한 지점에서 쟀는지 본다.

    sweep의 이분 탐색이 찾은 지점은 실행마다 달라질 수 있다. 그 값으로 baseline을
    굳히면 구성 키가 불안정해 다시 만날 실행이 없다. 실제로 0.15 rps에서 승격했는데
    선언된 지점은 0.1·0.2·0.5·1.0이었고, 이후 어떤 실행도 그 baseline과 맞지 않았다.
    """
    from scripts.benchmark.sweep import sweep_axis

    axis, declared = sweep_axis(contract, document["workload"]["id"])
    configuration = configuration_of(document)
    if axis not in configuration:
        return
    value = float(configuration[axis])
    if not any(abs(value - point) < 1e-9 for point in declared):
        raise PromotionRefused(
            f"{axis}={value:g} is not a declared sweep point {declared}; 재현되지 않는 지점에서 "
            "굳히면 이 baseline과 만날 실행이 없다"
        )


def _minimum_samples(contract: PerformanceContract) -> dict[str, int]:
    return {
        name: int(value)
        for name, value in (contract.statistics.get("minimum_samples") or {}).items()
    }


def promote(
    documents: list[dict[str, Any]],
    contract: PerformanceContract,
    *,
    promoted_by: str,
    note: str = "",
) -> dict[str, Any]:
    """실행 하나 이상을 baseline으로 만든다.

    여럿을 넘기면 통계마다 실행 간 편차를 함께 담는다. 허용 오차가 그 편차보다
    작으면 정상 변동을 회귀로 잡게 되므로, 오차를 정할 때 볼 수 있어야 한다.
    """
    if not documents:
        raise PromotionRefused("no documents to promote")
    rules = policy().get("promotion") or {}
    compared = policy().get("compared") or {}
    minimum = _minimum_samples(contract)

    configurations = [configuration_of(document) for document in documents]
    if any(configuration != configurations[0] for configuration in configurations[1:]):
        raise PromotionRefused(
            "runs come from different configurations; 서로 다른 구성의 관찰을 한 baseline으로 "
            "합치면 어느 구성의 값도 아니게 된다"
        )
    for document in documents:
        _assert_promotable(document, contract, rules)

    statistics_document: dict[str, dict[str, Any]] = {}
    for metric_name, wanted in compared.items():
        for statistic in wanted:
            values, samples = [], []
            for document in documents:
                entry = (document.get("summary") or {}).get(metric_name) or {}
                value = entry.get(statistic)
                if value is None:
                    continue
                count = int(entry.get("count", 0))
                floor = minimum.get(statistic)
                if floor is None:
                    # percentile이 아닌 통계에는 slo.yaml이 최소를 선언하지 않는다.
                    # 선언된 것 중 가장 약한 기준을 바닥으로 쓴다.
                    floor = minimum.get(str(rules.get("aggregate_minimum_samples_from", "p50")), 1)
                if rules.get("require_minimum_samples", True) and count < floor:
                    continue
                values.append(float(value))
                samples.append(count)
            if not values:
                continue
            record: dict[str, Any] = {
                "value": statistics.median(values),
                "samples": min(samples) if samples else 0,
            }
            if len(values) > 1:
                middle = statistics.median(values)
                if middle > 0:
                    record["observed_spread_ratio"] = (max(values) - min(values)) / middle
            statistics_document.setdefault(metric_name, {})[statistic] = record

    if not statistics_document:
        raise PromotionRefused(
            "no comparable statistic met the promotion rules; 표본을 더 모으거나 용량 안에서 "
            "다시 재야 한다"
        )
    environment = documents[0]["environment"]
    return {
        "schema_version": 1,
        "contract_version": contract.version,
        "configuration": configurations[0],
        "provenance": {
            "run_ids": [document["run"]["id"] for document in documents],
            "git_commit": environment["git_commit"],
            "platform_image": environment.get("platform_image", ""),
            "promoted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "promoted_by": promoted_by,
            **({"note": note} if note else {}),
        },
        "statistics": statistics_document,
    }


def load_baselines(directory: Path | None = None) -> list[dict[str, Any]]:
    target = directory or BASELINE_DIR
    if not target.is_dir():
        return []
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(target.glob("*.json"))]


def baseline_for(document: dict[str, Any], baselines: list[dict[str, Any]]) -> dict[str, Any] | None:
    """이 실행과 구성이 일치하는 baseline.

    일치하지 않으면 비교하지 않는다. 다른 구성의 관찰과 견주면 성능이 변한 것이
    아니라 구성이 다른 것을 회귀로 읽는다.
    """
    configuration = configuration_of(document)
    for candidate in baselines:
        if candidate.get("configuration") == configuration:
            return candidate
    return None


def compare(
    document: dict[str, Any], baseline: dict[str, Any], contract: PerformanceContract
) -> dict[str, Any]:
    """새 실행을 baseline과 견준다.

    방향은 계약이 안다. 지연은 커지면 회귀이고 처리량은 작아지면 회귀다.
    """
    tolerance = float((policy().get("tolerance") or {}).get("default_ratio", 0.1))
    results: list[dict[str, Any]] = []
    for metric_name, entries in (baseline.get("statistics") or {}).items():
        better = str(contract.metrics.get(metric_name, {}).get("better", "lower"))
        for statistic, record in entries.items():
            observed = ((document.get("summary") or {}).get(metric_name) or {}).get(statistic)
            reference = float(record["value"])
            allowed = max(tolerance, float(record.get("observed_spread_ratio", 0.0)))
            entry: dict[str, Any] = {
                "metric": metric_name,
                "statistic": statistic,
                "baseline": reference,
                "observed": observed,
                "tolerance_ratio": allowed,
            }
            if observed is None:
                entry["status"] = "not_evaluated"
                entry["reason"] = "this run produced no value for that statistic"
            else:
                change = (float(observed) - reference) / reference if reference else 0.0
                entry["change_ratio"] = change
                worse = -change if better == "higher" else change
                entry["status"] = "regressed" if worse > allowed else "ok"
            results.append(entry)
    regressed = [entry for entry in results if entry["status"] == "regressed"]
    evaluated = [entry for entry in results if entry["status"] != "not_evaluated"]
    return {
        "baseline_run_ids": list(baseline["provenance"]["run_ids"]),
        "status": "regressed" if regressed else ("ok" if evaluated else "not_evaluated"),
        "comparisons": results,
    }
