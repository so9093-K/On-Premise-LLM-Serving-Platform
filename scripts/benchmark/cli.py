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
    try:
        document = run_workload(contract, options)
    except (RunnerError, KeyError) as exc:
        print(f"[perf] 실행을 중단했습니다: {exc}", file=sys.stderr)
        return 2

    try:
        document = evaluator.evaluate(document, contract)
    except evaluator.EvaluationError as exc:
        print(f"[perf] 집계를 중단했습니다: {exc}", file=sys.stderr)
        return 4

    directory = Path(args.output_dir).resolve() if args.output_dir else None
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
