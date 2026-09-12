"""결과 JSON에서 보고서를 뽑는다(ADR-0026 9절). 측정도 계산도 하지 않는다."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from scripts.benchmark.contract import load_contract  # noqa: E402
from scripts.benchmark.report import render  # noqa: E402
from scripts.benchmark.result import REPORTS_DIR  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="측정 결과 JSON에서 읽을 수 있는 보고서를 만듭니다. 숫자를 다시 계산하지 않습니다.",
    )
    parser.add_argument("results", nargs="*", help="결과 파일. 비우면 reports/performance/의 가장 최근 것.")
    parser.add_argument("--output", default="", help="저장 경로. 비우면 표준 출력.")
    return parser


def _latest() -> Path:
    candidates = [p for p in sorted(REPORTS_DIR.glob("*.json")) if not p.name.endswith(".sweep.json")]
    if not candidates:
        raise SystemExit(f"[report] {REPORTS_DIR}에 결과가 없습니다. 측정을 먼저 실행하세요.")
    return candidates[-1]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = [Path(item) for item in args.results] or [_latest()]
    contract = load_contract()
    rendered = "\n".join(
        render(json.loads(path.read_text(encoding="utf-8")), contract) for path in paths
    )
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
        print(f"[report] {target}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
