"""결과 JSON에서 읽을 수 있는 보고서를 만든다(ADR-0026 9절).

원본은 JSON이고 이것은 파생이다. 그래서 여기서 계산하지 않는다 -- 집계는
evaluator가, 판정은 회귀 비교와 게이트가 이미 했다. 여기서 다시 계산하면 보고서와
결과 파일이 다른 숫자를 말하게 된다.

Prometheus로 밀어 넣지 않는다. 벤치마크는 간헐적으로 도는 배치라 스크레이프 모델과
맞지 않고, 그러자고 컴포넌트를 하나 더 두면 결과를 읽는 길이 둘이 된다.
"""
from __future__ import annotations

from typing import Any

from scripts.benchmark.contract import PerformanceContract

_UNIT_SUFFIX = {"seconds": "s", "ratio": "", "requests_per_second": " rps",
                "tokens_per_second": " tok/s", "count": "", "bytes": " B"}


def _format(value: Any, unit: str) -> str:
    if value is None:
        return "-"
    if not isinstance(value, (int, float)):
        return str(value)
    if unit == "ratio":
        return f"{value * 100:.1f}%"
    if unit == "seconds" and value < 1:
        return f"{value * 1000:.1f} ms"
    return f"{value:,.4g}{_UNIT_SUFFIX.get(unit, '')}"


def _environment_rows(document: dict[str, Any]) -> list[str]:
    environment = document.get("environment") or {}
    gpu = environment.get("gpu") or {}
    workload = document.get("workload") or {}
    traffic = workload.get("traffic") or {}
    load = traffic.get("request_rate_per_second")
    load_text = f"{load:g} rps" if load is not None else (
        f"동시성 {traffic['concurrency']}" if "concurrency" in traffic else "-"
    )
    rows = [
        ("workload", f"{workload.get('id', '-')} ({workload.get('cache_policy', '-')} cache)"),
        ("부하", load_text),
        ("입력 길이", f"{workload['input_tokens']:,} 토큰" if "input_tokens" in workload else "-"),
        ("런타임", f"{environment.get('runtime_backend', '-')} / {environment.get('runtime_profile', '-')}"),
        ("모델", f"{environment.get('model_id', '-')} @ {str(environment.get('model_revision', ''))[:8]}"),
        ("가속기", f"{gpu.get('model')} ({gpu.get('memory_kind')})" if gpu
         else environment.get("accelerator_unavailable_reason", "-")),
        ("커밋", str(environment.get("git_commit", ""))[:12]),
    ]
    return [f"| {name} | {value} |" for name, value in rows]


def render(document: dict[str, Any], contract: PerformanceContract) -> str:
    """실행 하나를 Markdown으로. 숫자는 결과 파일의 것을 그대로 쓴다."""
    run = document["run"]
    lines = [
        f"# 성능 측정 보고서: {run['id']}",
        "",
        f"측정 시각 {run['started_at']}, 구간 {run['duration_seconds']:.0f}초, "
        f"요청 {len(document['requests'])}건",
        "",
        "## 측정 조건",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        *_environment_rows(document),
        "",
        "## 집계",
    ]
    lines += _summary_sections(document, contract)

    lines += _verdict_section(document)
    lines += _regression_section(document)
    lines += _runtime_section(document)
    lines += [
        "",
        "---",
        "",
        "이 보고서는 결과 JSON에서 파생된다. 숫자를 여기서 다시 계산하지 않는다.",
        "원문 프롬프트와 모델 출력은 기록하지 않는다.",
    ]
    return "\n".join(lines) + "\n"


def _number(value: Any, digits: str = ".4g") -> str:
    """표의 한 칸. 값이 없으면 빈 칸이 아니라 -로 적는다."""
    return format(value, digits) if isinstance(value, (int, float)) else "-"


def _summary_sections(document: dict[str, Any], contract: PerformanceContract) -> list[str]:
    """분포와 단일 값을 한 표에 섞지 않는다.

    섞으면 단일 값이 percentile 칸에 들어간다. 실제로 chunk 간격의 합계 109.5초가
    p50 칸에 나왔고, 그 자리에서는 chunk 하나당 시간으로 읽힌다.
    """
    summary = document.get("summary") or {}
    distributions, singles = [], []
    for name, entry in sorted(summary.items()):
        metric = contract.metrics.get(name) or {}
        unit = str(metric.get("unit", ""))
        role = str(metric.get("role", "-"))
        count = entry.get("count", "-")
        percentiles = [key for key in ("p50", "p95", "p99") if key in entry]
        if percentiles:
            cells = " | ".join(_format(entry.get(key), unit) for key in ("p50", "p95", "p99"))
            distributions.append(f"| {name} | {role} | {cells} | {count} |")
        for key in ("value", "mean"):
            if key in entry:
                label = {"value": "합계 또는 창 단위 값", "mean": "평균"}[key]
                singles.append(f"| {name} | {role} | {label} | {_format(entry[key], unit)} | {count} |")

    lines: list[str] = []
    if distributions:
        lines += ["", "### 분포", "", "| 지표 | 역할 | p50 | p95 | p99 | 표본 |",
                  "|---|---|---:|---:|---:|---:|", *distributions]
    if singles:
        lines += ["", "### 단일 값", "", "| 지표 | 역할 | 통계 | 값 | 표본 |",
                  "|---|---|---|---:|---:|", *singles]
    return lines


def _verdict_section(document: dict[str, Any]) -> list[str]:
    verdict = document.get("verdict")
    if not verdict:
        return []
    reasons = {"insufficient_samples": "표본 부족", "no_threshold": "임계값 미정",
               "not_measured": "측정값 없음"}
    lines = [
        "",
        f"## SLO 판정: {verdict['status']} (적용 수준 {verdict['enforcement']})",
        "",
        "| 지표 | 통계 | 관측 | 임계값 | 출처 | 결과 |",
        "|---|---|---:|---:|---|---|",
    ]
    for entry in verdict.get("objectives") or []:
        status = entry["status"]
        if status == "not_evaluated":
            reason = reasons.get(str(entry.get("not_evaluated_reason", "")), "")
            status = f"{status} ({reason})" if reason else status
        lines.append(
            f"| {entry['metric']} | {entry['statistic']} "
            f"| {_number(entry.get('observed'))} | {_number(entry.get('threshold'), 'g')} "
            f"| {entry.get('threshold_source') or '-'} | {status} |"
        )
    return lines


def _regression_section(document: dict[str, Any]) -> list[str]:
    regression = document.get("regression")
    if not regression:
        return ["", "## baseline 비교", "",
                "이 구성의 baseline이 없습니다. 견줄 대상이 없다는 뜻이며, 통과가 아닙니다."]
    lines = [
        "",
        f"## baseline 비교: {regression['status']}",
        "",
        f"기준 실행: {', '.join(regression.get('baseline_run_ids') or []) or '-'}",
        "",
        "| 지표 | 통계 | baseline | 관측 | 변화 | 허용 | 결과 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for entry in regression.get("comparisons") or []:
        change = entry.get("change_ratio")
        change_text = f"{change * 100:+.1f}%" if isinstance(change, (int, float)) else "-"
        tolerance = entry.get("tolerance_ratio")
        tolerance_text = f"{tolerance * 100:.0f}%" if isinstance(tolerance, (int, float)) else "-"
        lines.append(
            f"| {entry['metric']} | {entry['statistic']} "
            f"| {_number(entry.get('baseline'))} | {_number(entry.get('observed'))} "
            f"| {change_text} | {tolerance_text} | {entry['status']} |"
        )
    return lines


def _runtime_section(document: dict[str, Any]) -> list[str]:
    snapshot = document.get("runtime_snapshot") or {}
    skipped = document.get("runtime_snapshot_skipped") or {}
    error = document.get("runtime_snapshot_error")
    if error:
        return ["", "## 런타임 상태", "", f"읽지 못했습니다: {error}"]
    if not snapshot and not skipped:
        return []
    lines = ["", "## 런타임 상태", "", "| 지표 | 시작 | 끝 | 평균 | 최대 | scrape |",
             "|---|---:|---:|---:|---:|---:|"]
    for name, entry in sorted(snapshot.items()):
        lines.append(
            f"| {name} | {_number(entry.get('start'), '.6g')} | {_number(entry.get('end'), '.6g')} "
            f"| {_number(entry.get('avg'))} | {_number(entry.get('max'))} "
            f"| {entry.get('samples', '-')} |"
        )
    for name in sorted(skipped):
        lines.append(f"| {name} | - | - | - | - | 건너뜀 |")
    if skipped:
        lines += ["", "건너뛴 이유:", ""]
        lines += [f"- `{name}`: {reason}" for name, reason in sorted(skipped.items())]
    return lines
