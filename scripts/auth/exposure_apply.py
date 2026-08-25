from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.cli_kr import KoreanArgumentParser  # noqa: E402
from scripts.lib.env_path import resolve_env_path  # noqa: E402
from scripts.auth.exposure_plan import build_plan, render_plan  # noqa: E402
from scripts.config.setup_env import parse_env_template, write_env  # noqa: E402




def build_parser() -> KoreanArgumentParser:
    parser = KoreanArgumentParser(
        description="EXPOSURE_MODE와 EXPOSURE_AUDIENCE를 env 파일에 적용합니다. "
                    "--yes 없이 실행하면 plan만 출력합니다."
    )
    parser.add_argument("--mode", required=True, help="대상 EXPOSURE_MODE (private_network|master_open)")
    parser.add_argument("--audience", help="EXPOSURE_AUDIENCE (local_only|private_lan|vpn|public)")
    parser.add_argument("--env", default=".env", help="업데이트할 env 파일. 기본값은 repository root 기준.")
    parser.add_argument("--yes", action="store_true", help="env 파일에 실제로 기록합니다.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env_path = resolve_env_path(args.env)

    try:
        if env_path.exists():
            lines, current = parse_env_template(env_path)
        else:
            lines, current = [], {}
    except RuntimeError as exc:
        print(f"env 파일 오류: {exc}", file=sys.stderr)
        return 2

    plan = build_plan(current, args.mode, args.audience)
    print(render_plan(plan), end="")

    if not args.yes:
        print("dry-run입니다. env 파일은 변경하지 않았습니다. 기록하려면 --yes를 붙여 다시 실행하세요.")
        return 0

    target: dict[str, str] = {"EXPOSURE_MODE": args.mode}
    if args.audience is not None:
        target["EXPOSURE_AUDIENCE"] = args.audience

    current.update(target)
    write_env(lines, current, env_path)
    print(f"업데이트 완료: {env_path}")
    print("안내: 변경사항을 compose에 반영하려면 스택을 재기동하세요.")
    print("  make compose-down && make compose-up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
