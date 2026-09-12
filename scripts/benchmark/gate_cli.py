"""성능 게이트 진입점. 이미 측정된 결과를 판정만 한다(ADR-0026 14절)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from scripts.benchmark.contract import load_contract  # noqa: E402
from scripts.benchmark.gate import GateError, judge, load_results  # noqa: E402
from scripts.benchmark.result import REPORTS_DIR  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="측정된 성능 결과로 릴리스 자격을 판정합니다. 측정은 하지 않습니다.",
    )
    parser.add_argument(
        "results", nargs="*", default=None,
        help="판정할 결과 파일이나 디렉터리. 기본은 reports/performance/.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = [Path(item) for item in (args.results or [])] or [REPORTS_DIR]
    try:
        outcome = judge(load_results(paths), load_contract())
    except GateError as exc:
        print(f"[perf-gate] 판정할 수 없습니다: {exc}", file=sys.stderr)
        return 2

    for entry in outcome["checked"]:
        print(f"[perf-gate] {entry['check']:10} {entry['enforcement']:8} {entry['status']}"
              f"  ({entry['run']})")
    for entry in outcome["skipped"]:
        print(f"[perf-gate] {entry['check']:10} {'건너뜀':8} {entry['reason']}")
    for entry in outcome["blocked"]:
        for detail in entry["detail"]:
            print(f"[perf-gate]   막힘: {detail}", file=sys.stderr)

    if outcome["status"] == "fail":
        print("[perf-gate] 릴리스 자격 실패", file=sys.stderr)
        return 1
    print("[perf-gate] 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
