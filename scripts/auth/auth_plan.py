from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from scripts.lib.cli_kr import KoreanArgumentParser  # noqa: E402
from ai_model_serving.auth_control import (  # noqa: E402
    AUTH_MODE_EXPECTATIONS,
    AUTH_PROFILE_ENV_KEYS,
    auth_profile_exposure_values,
    auth_profile_env_values,
    auth_profile_summary,
)
from scripts.config.setup_env import parse_env_template  # noqa: E402

MANAGED_MODES = tuple(mode for mode in AUTH_MODE_EXPECTATIONS if mode != "custom")
NON_LOCAL_ENVS = {"staging", "production", "prod"}


def _env_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def load_current(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    _, values = parse_env_template(path)
    return values


def build_plan(current: dict[str, str], mode: str, *, app_env: str | None = None) -> dict[str, Any]:
    target = auth_profile_env_values(mode)
    target.update(auth_profile_exposure_values(mode))
    if app_env:
        target["APP_ENV"] = app_env
    changes = []
    for key in ("APP_ENV", *AUTH_PROFILE_ENV_KEYS, "EXPOSURE_MODE", "EXPOSURE_AUDIENCE"):
        if key not in target:
            continue
        before = current.get(key, "<unset>")
        after = target[key]
        changes.append({"key": key, "before": before, "after": after, "changed": before != after})
    effective_env = (app_env or current.get("APP_ENV") or ("local" if mode == "local_open" else "staging")).lower()
    warnings: list[str] = []
    if mode == "local_open" and effective_env not in {"local", "test", "development"}:
        warnings.append(
            "local_open은 API/admin/internal 인증을 끄고 master_open/private_lan으로 "
            "전체 stack을 host-publish합니다. 외부 접근이 차단된 신뢰된 사내망에서만 사용하세요."
        )
    if mode == "edge_terminated":
        warnings.append("edge_terminated는 외부 proxy가 public /v1/* traffic을 인증한다고 가정합니다. admin/internal token은 켜 둬야 합니다.")
    if mode in {"private_network", "strict"} and target.get("API_KEY_REQUIRED") != "true":
        warnings.append("managed profile invariant가 깨졌습니다. public API는 Gateway key를 요구해야 합니다.")
    return {
        "target_mode": mode,
        "scope": auth_profile_summary(mode),
        "env_changes": changes,
        "warnings": warnings,
    }


def render_plan(plan: dict[str, Any]) -> str:
    lines = [
        f"대상 인증 모드: {plan['target_mode']}",
        f"적용 범위: {plan['scope']}",
        "",
        "변경 예정 env flag",
        "KEY                            현재값                         변경값",
        "---                            ------                         -----",
    ]
    for change in plan["env_changes"]:
        marker = "*" if change["changed"] else " "
        lines.append(f"{marker} {change['key']:<30} {change['before']:<30} {change['after']}")
    if plan["warnings"]:
        lines.extend(["", "주의"] )
        lines.extend(f"- {warning}" for warning in plan["warnings"])
    lines.append("")
    lines.append("auth-plan은 secret 값을 표시하거나 변경하지 않습니다.")
    return "\n".join(lines) + "\n"


def build_parser() -> KoreanArgumentParser:
    parser = KoreanArgumentParser(description="secret을 노출하지 않고 managed auth profile flag 변경 계획을 표시합니다.")
    parser.add_argument("--mode", choices=MANAGED_MODES, required=True)
    parser.add_argument("--env", default=".env", help="점검할 env 파일입니다. 기본값은 repository root 기준입니다.")
    parser.add_argument("--app-env", help="auth flag와 함께 APP_ENV 변경도 계획합니다.")
    parser.add_argument("--json", action="store_true", help="기계가 읽기 쉬운 JSON을 출력합니다.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        current = load_current(_env_path(args.env))
    except RuntimeError as exc:
        print(f"env 파일 오류: {exc}", file=sys.stderr)
        return 2
    plan = build_plan(current, args.mode, app_env=args.app_env)
    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    else:
        print(render_plan(plan), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
