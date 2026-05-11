from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from scripts.lib.cli_kr import KoreanArgumentParser  # noqa: E402
from ai_model_serving.auth_control import AUTH_MODE_EXPECTATIONS, auth_profile_env_values  # noqa: E402
from scripts.auth.auth_plan import build_plan, render_plan  # noqa: E402
from scripts.config.setup_env import parse_env_template, write_env  # noqa: E402

MANAGED_MODES = tuple(mode for mode in AUTH_MODE_EXPECTATIONS if mode != "custom")

_APP_ENV_FOR_MODE: dict[str, str] = {
    "local_open": "local",
}


def _env_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def build_parser() -> KoreanArgumentParser:
    parser = KoreanArgumentParser(description="secret을 변경하지 않고 managed auth profile을 env 파일에 적용합니다.")
    parser.add_argument("--mode", choices=MANAGED_MODES, required=True)
    parser.add_argument("--env", default=".env", help="업데이트할 env 파일입니다. 기본값은 repository root 기준입니다.")
    parser.add_argument("--app-env", help="auth flag와 함께 APP_ENV도 업데이트합니다.")
    parser.add_argument("--yes", action="store_true", help="env 파일에 실제로 기록합니다. 없으면 plan만 출력합니다.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env_path = _env_path(args.env)
    if env_path.exists():
        lines, values = parse_env_template(env_path)
    else:
        lines, values = [], {}
    target = auth_profile_env_values(args.mode)
    resolved_app_env = args.app_env or _APP_ENV_FOR_MODE.get(args.mode)
    if resolved_app_env:
        target["APP_ENV"] = resolved_app_env
    plan = build_plan(values, args.mode, app_env=resolved_app_env)
    print(render_plan(plan), end="")
    if not args.yes:
        print("dry-run입니다. env 파일은 변경하지 않았습니다. 기록하려면 --yes를 붙여 다시 실행하세요.")
        return 0
    values.update(target)
    write_env(lines, values, env_path)
    print(f"업데이트 완료: {env_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
