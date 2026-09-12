"""baseline 승격 진입점(ADR-0026 9절).

승격은 사람이 한다. 도구가 스스로 올리면 성능이 서서히 나빠져도 baseline이 따라
내려가 회귀를 영원히 못 잡는다. 그래서 이 명령은 대상 파일과 승격자를 명시적으로
받고, 승격 조건을 통과하지 못하면 거부한다.

결과물은 저장소가 소유하는 artifact이므로 커밋이 리뷰 대상이다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (str(ROOT), str(ROOT / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from scripts.benchmark import result  # noqa: E402
from scripts.benchmark.baseline import (  # noqa: E402
    BASELINE_DIR,
    BASELINE_SCHEMA_PATH,
    PromotionRefused,
    promote,
    slug,
)
from scripts.benchmark.contract import load_contract  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="측정 결과를 baseline으로 승격합니다. 승격은 사람이 명시적으로 합니다.",
    )
    parser.add_argument("results", nargs="+", help="승격할 결과 파일. 여럿이면 실행 간 편차를 함께 담습니다.")
    parser.add_argument("--by", required=True, help="승격하는 사람. 리뷰에서 누가 올렸는지 보여야 합니다.")
    parser.add_argument("--note", default="", help="무엇을 어떤 조건에서 쟀는지 한 줄.")
    parser.add_argument(
        "--output-dir", default="",
        help=f"저장 위치. 기본은 {BASELINE_DIR.relative_to(ROOT)}.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    documents = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.results]
    try:
        baseline = promote(documents, load_contract(), promoted_by=args.by, note=args.note)
    except PromotionRefused as exc:
        print(f"[promote] 승격을 거부했습니다: {exc}", file=sys.stderr)
        return 2

    directory = Path(args.output_dir).resolve() if args.output_dir else BASELINE_DIR
    try:
        # 저장소가 소유하는 artifact다. 검증하지 않은 파일을 커밋하지 않는다.
        result.validate(baseline, BASELINE_SCHEMA_PATH)
    except result.ResultValidationError as exc:
        print(f"[promote] {exc}", file=sys.stderr)
        return 3

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug(baseline['configuration'])}.json"
    existing = path.exists()
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[promote] {'갱신' if existing else '생성'}: {path}")
    print(f"[promote] 실행 {len(documents)}개, 승격자 {args.by}")
    for metric, entries in sorted(baseline["statistics"].items()):
        for statistic, record in sorted(entries.items()):
            spread = record.get("observed_spread_ratio")
            suffix = f"  실행 간 편차 {spread * 100:.1f}%" if spread is not None else ""
            print(f"[promote]   {metric} {statistic}: {record['value']:.5g}"
                  f" (표본 {record['samples']}){suffix}")
    if existing:
        # 덮어쓰는 것이 정상일 수도 있지만, 무엇이 바뀌는지 리뷰에서 보여야 한다.
        print("[promote] 기존 baseline을 덮어썼습니다. 커밋 전에 diff를 확인하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
