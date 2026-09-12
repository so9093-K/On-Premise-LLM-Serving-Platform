"""성능 벤치마크 실행 진입점(ADR-0026).

계약을 읽고, workload를 실행하고, schema를 통과한 결과만 reports에 남긴다.
집계와 SLO 판정은 하지 않는다. 그 단계는 evaluator가 이 파일들을 입력으로 받는다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from scripts.benchmark import evaluate as evaluator  # noqa: E402
from scripts.benchmark import result  # noqa: E402
from scripts.lib.process_env import load_dotenv  # noqa: E402
from scripts.benchmark.contract import load_contract  # noqa: E402
from scripts.benchmark.runner import RunOptions, RunnerError, gateway_endpoint, run_workload  # noqa: E402
from scripts.benchmark.snapshot import PrometheusReader, SnapshotUnavailable, collect  # noqa: E402
from scripts.benchmark.snapshot import prometheus_base  # noqa: E402
from scripts.benchmark.sweep import run_sweep  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="성능 벤치마크를 실행하고 원시 결과를 저장합니다.")
    parser.add_argument("--workload", default="interactive", help="configs/performance/workloads.yaml의 workload id")
    parser.add_argument(
        "--mode", default="smoke", choices=["smoke", "baseline", "sweep", "regression"],
        help="이 실행의 성격. baseline 승격 여부를 이 값으로 구분한다.",
    )
    parser.add_argument("--seed", type=int, default=1, help="프롬프트 생성 seed. 같은 seed는 같은 프롬프트를 만든다.")
    parser.add_argument("--gateway-base", default="", help="Gateway base URL. 비우면 configs/services.yaml에서 해석한다.")
    parser.add_argument(
        "--requests", type=int, default=None,
        help="측정 구간 요청 수를 직접 지정한다. smoke 실행용이며 지정하면 measurement_seconds를 대신한다.",
    )
    parser.add_argument("--measurement-seconds", type=float, default=None, help="workload의 measurement_seconds를 덮어쓴다.")
    parser.add_argument("--warmup-seconds", type=float, default=None, help="workload의 warmup_seconds를 덮어쓴다.")
    parser.add_argument("--output-dir", default="", help="결과 저장 위치. 기본은 reports/performance/.")
    parser.add_argument(
        "--prometheus-base", default="",
        help="런타임 상태를 읽을 Prometheus 주소. 비우면 configs/services.yaml에서 해석한다. "
             "기본 노출 프로필은 이 주소를 호스트에 공개하지 않으므로 닿지 못할 수 있고, "
             "그때는 이유를 결과에 남긴다.",
    )
    parser.add_argument(
        "--sweep", action="store_true",
        help="계약이 선언한 request_rate_sweep의 모든 지점을 낮은 쪽부터 실행해 용량을 찾습니다.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # runtime validation과 같은 규칙으로 .env를 읽는다. export된 값이 우선한다.
    load_dotenv(ROOT)
    base, api_key = gateway_endpoint(args.gateway_base)
    contract = load_contract()
    options = RunOptions(
        workload_id=args.workload,
        mode=args.mode,
        gateway_base=base,
        api_key=api_key,
        seed=args.seed,
        max_requests=args.requests,
        measurement_seconds=args.measurement_seconds,
        warmup_seconds=args.warmup_seconds,
    )
    directory = Path(args.output_dir).resolve() if args.output_dir else None
    if args.sweep:
        return _run_sweep(contract, options, directory, base, args.prometheus_base)

    try:
        document = run_workload(contract, options)
    except (RunnerError, KeyError) as exc:
        print(f"[perf] 실행을 중단했습니다: {exc}", file=sys.stderr)
        return 2

    document = _with_runtime_snapshot(document, contract, args.prometheus_base)
    try:
        document = evaluator.evaluate(document, contract)
    except evaluator.EvaluationError as exc:
        print(f"[perf] 집계를 중단했습니다: {exc}", file=sys.stderr)
        return 4

    try:
        path = result.write(document, directory=directory)
    except result.ResultValidationError as exc:
        print(f"[perf] {exc}", file=sys.stderr)
        return 3

    requests = document["requests"]
    failed = [sample for sample in requests if not sample["succeeded"]]
    print(f"[perf] workload={args.workload} mode={args.mode} target={base}")
    print(f"[perf] 요청 {len(requests)}건, 실패 {len(failed)}건, dispatch lag 최대 "
          f"{document['run']['max_dispatch_lag_seconds']:.3f}s")
    if failed:
        codes = sorted({str(sample.get("error_code") or sample.get("status_code")) for sample in failed})
        print(f"[perf] 실패 사유: {', '.join(codes)}")
    _print_verdict(document)
    print(f"[perf] 결과: {path}")
    # 실패한 요청이 있어도 결과는 남긴다. 판정은 evaluator의 일이다.
    return 0


def _with_runtime_snapshot(document: dict, contract, override: str) -> dict:
    """런타임 상태를 붙인다. 못 읽으면 이유를 남긴다.

    수집하지 않은 것과 수집했는데 비어 있던 것은 다르다. 조용히 비워 두면 런타임이
    한가했던 것으로 읽힌다.
    """
    try:
        reader = PrometheusReader(prometheus_base(override))
        snapshot = collect(contract, document, reader)
    except SnapshotUnavailable as exc:
        return {**document, "runtime_snapshot_error": str(exc)}
    return {**document, "runtime_snapshot": snapshot} if snapshot else document


def _run_sweep(contract, options: RunOptions, directory, base: str, prometheus_override: str) -> int:
    """지점마다 결과 문서를 따로 남기고, 마지막에 감당한 부하를 보고한다."""
    print(f"[perf] sweep workload={options.workload_id} target={base}")
    paths: list[Path] = []
    failed: list[str] = []

    def report(point: dict, axis: str, document: dict) -> None:
        """지점이 끝나는 대로 저장하고 사실만 알린다.

        판정은 인쇄하지 않는다. 동시성 sweep의 판정은 지점 간 비교라 모든 지점을
        모은 뒤에야 정해진다. 먼저 찍으면 아직 계산되지 않은 값을 보여주게 되고,
        실제로 세 지점이 모두 "처리량 증가"로 나오면서 결론과 어긋났다.

        저장도 여기서 한다. 끝까지 모아 두면 표시 한 줄이 잘못돼도 이미 측정한
        몇 분치가 통째로 사라진다 -- 실제로 그렇게 잃었다.
        """
        try:
            document = _with_runtime_snapshot(document, contract, prometheus_override)
            paths.append(result.write(document, directory=directory))
        except result.ResultValidationError as exc:
            failed.append(str(exc))
        throughput = point["output_tokens_per_second"]
        throughput_text = f"{throughput:.1f} tok/s" if throughput is not None else "-"
        print(f"[perf]   {_axis_label(axis)} {point[axis]:g}: "
              f"성공률 {point['success_ratio']:.0%}, {throughput_text}")

    try:
        outcome, _ = run_sweep(contract, options, on_point=report)
    except (RunnerError, KeyError) as exc:
        print(f"[perf] sweep을 중단했습니다: {exc}", file=sys.stderr)
        return 2

    if failed:
        print(f"[perf] {failed[0]}", file=sys.stderr)
        return 3
    try:
        summary_path = result.write_sweep(outcome, directory=directory)
    except result.ResultValidationError as exc:
        print(f"[perf] {exc}", file=sys.stderr)
        return 3

    sustained = outcome["sustained_point"]
    declared = outcome["declared_point"]
    unit = _axis_unit(outcome["axis"])
    concurrency_axis = outcome["axis"] == "concurrency"
    signal_header = {"input_tokens": "첫 응답 평균", "concurrency": "변화"}.get(outcome["axis"], "지연 증가")
    print(f"[perf] {_axis_label(outcome['axis']):>9} {'성공률':>7} {'처리량':>11} {signal_header:>9}  판정")
    axis = outcome["axis"]
    for point in outcome["points"]:
        load = point[axis]
        throughput = point["output_tokens_per_second"]
        throughput_text = f"{throughput:.1f} tok/s" if throughput is not None else "-"
        drift = point.get("time_to_first_chunk_drift")
        gain = point.get("throughput_gain_ratio")
        if concurrency_axis:
            signal = "기준" if gain is None else f"{gain:+.0%}"
            verdict = "처리량 증가" if point["sustained"] else "더 안 나옴"
        elif axis == "input_tokens":
            ttfc = point.get("mean_time_to_first_chunk_seconds")
            signal = f"{ttfc:.2f}s" if ttfc is not None else "-"
            verdict = "처리함" if point["sustained"] else "실패"
        else:
            signal = f"{drift:.2f}x" if drift is not None else "-"
            verdict = "감당" if point["sustained"] else "대기 쌓임"
        print(f"[perf] {load:9.6g} {point['success_ratio']:6.0%} {throughput_text:>11} "
              f"{signal:>9}  {verdict}")

    if sustained is None:
        print("[perf] 감당한 지점이 없습니다. 가장 낮은 지점부터 대기가 쌓입니다.")
    elif outcome["axis"] == "input_tokens":
        print(f"[perf] 처리한 가장 긴 입력: {sustained:g} {unit} "
              f"(계약 선언 {declared:g} {unit})")
    elif concurrency_axis:
        print(f"[perf] 처리량이 늘어나는 한계: {sustained:g} {unit} "
              f"(계약 선언 {declared:g} {unit})")
        if sustained < declared:
            print("[perf] 그 위로는 동시에 더 돌려도 처리량이 늘지 않습니다. "
                  "런타임이 순차 처리하는지 확인하세요.")
    else:
        print(f"[perf] 감당하는 부하: {sustained:g} {unit} (계약 선언 {declared:g} {unit})")
        if sustained < declared:
            # 선언한 부하가 용량 밖이면 그 부하에서 잰 지연은 큐 길이다.
            print(f"[perf] 선언한 부하가 용량 밖입니다. {declared:g} {unit}에서 잰 지연은 "
                  "모델이 아니라 대기열을 재는 값입니다.")
    print(f"[perf] 지점별 결과 {len(paths)}건, 용량 요약: {summary_path.name}")
    return 0


_AXIS_LABELS = {"request_rate_per_second": "요청률", "concurrency": "동시성", "input_tokens": "입력 길이"}
_AXIS_UNITS = {"request_rate_per_second": "rps", "concurrency": "동시", "input_tokens": "토큰"}


def _axis_label(axis: str) -> str:
    return _AXIS_LABELS[axis]


def _axis_unit(axis: str) -> str:
    return _AXIS_UNITS[axis]


def _print_verdict(document: dict) -> None:
    """판정을 사람이 읽을 수 있게 줄인다. 근거는 결과 파일이 갖는다."""
    verdict = document["verdict"]
    print(f"[perf] SLO {verdict['slo_class']} ({verdict['enforcement']}): {verdict['status']}")
    reasons = {
        "insufficient_samples": "표본 부족",
        "no_threshold": "임계값 미정",
        "not_measured": "측정값 없음",
    }
    for objective in verdict["objectives"]:
        observed = objective["observed"]
        shown = f"{observed:.4g}" if isinstance(observed, (int, float)) else "-"
        status = objective["status"]
        if status == "not_evaluated":
            status = f"not_evaluated ({reasons[objective['not_evaluated_reason']]})"
        print(f"[perf]   {objective['metric']} {objective['statistic']}: {shown} -> {status}")


if __name__ == "__main__":
    raise SystemExit(main())
