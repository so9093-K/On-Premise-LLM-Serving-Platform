from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib.cli_kr import KoreanArgumentParser  # noqa: E402
from scripts.lib.env_path import load_env_values, resolve_env_path  # noqa: E402
from scripts.compose.resolve_exposure_mode import load_exposure_data  # noqa: E402

EXPOSURE_ENV_KEYS = ["EXPOSURE_MODE", "EXPOSURE_AUDIENCE"]






def build_plan(current: dict[str, str], mode: str, audience: str | None) -> dict[str, Any]:
    data = load_exposure_data(ROOT)
    profiles = data.get("profiles", {})
    allowed_audiences: list[str] = data.get("exposure_audience", {}).get("allowed_values", [])

    if mode not in profiles:
        raise SystemExit(
            f"지원하지 않는 EXPOSURE_MODE: {mode!r}. 지원값: {', '.join(profiles)}"
        )

    profile = profiles.get(mode, {})
    requires_audience: bool = profile.get("diagnostics", {}).get("requires_exposure_audience", False)

    target: dict[str, str] = {"EXPOSURE_MODE": mode}
    if audience is not None:
        if allowed_audiences and audience not in allowed_audiences:
            raise SystemExit(
                f"허용되지 않는 EXPOSURE_AUDIENCE: {audience!r}. 허용값: {', '.join(allowed_audiences)}"
            )
        target["EXPOSURE_AUDIENCE"] = audience
    elif requires_audience:
        target["EXPOSURE_AUDIENCE"] = current.get("EXPOSURE_AUDIENCE", "")

    changes = []
    for key in EXPOSURE_ENV_KEYS:
        if key not in target:
            continue
        before = current.get(key, "<unset>")
        after = target[key]
        changes.append({"key": key, "before": before, "after": after, "changed": before != after})

    warnings: list[str] = []
    effective_audience = audience or current.get("EXPOSURE_AUDIENCE", "")
    if requires_audience and not effective_audience:
        allowed_str = "|".join(allowed_audiences) if allowed_audiences else "local_only|private_lan|vpn|public"
        warnings.append(
            f"EXPOSURE_MODE={mode}은 EXPOSURE_AUDIENCE 설정이 필수입니다. "
            f"--audience {allowed_str} 중 하나를 지정하세요."
        )
    if effective_audience == "public":
        diag = profile.get("diagnostics", {})
        if diag.get("direct_model_runtime_access"):
            warnings.append(
                "EXPOSURE_AUDIENCE=public + direct_model_runtime_access=true: "
                "vLLM API가 인터넷에 직접 노출됩니다. firewall 또는 VPN 보호가 필요합니다."
            )
        if diag.get("direct_operations_endpoints"):
            warnings.append(
                "EXPOSURE_AUDIENCE=public + direct_operations_endpoints=true: "
                "Prometheus/DCGM/cAdvisor가 인터넷에 직접 노출됩니다. "
                "ALLOW_PUBLIC_OPERATIONS_ENDPOINTS=true를 명시적으로 설정해야 합니다."
            )

    return {
        "target_mode": mode,
        "description": profile.get("description", "").strip(),
        "host_published": profile.get("host_published", []),
        "diagnostics": profile.get("diagnostics", {}),
        "env_changes": changes,
        "warnings": warnings,
    }


def render_plan(plan: dict[str, Any]) -> str:
    lines = [
        f"대상 노출 모드: {plan['target_mode']}",
        f"설명: {plan['description']}",
        "",
        f"Host-published 서비스: {', '.join(plan['host_published']) if plan['host_published'] else '(없음)'}",
        "",
        "변경 예정 env flag",
        "KEY                            현재값                         변경값",
        "---                            ------                         -----",
    ]
    for change in plan["env_changes"]:
        marker = "*" if change["changed"] else " "
        lines.append(f"{marker} {change['key']:<30} {change['before']:<30} {change['after']}")
    diag = plan.get("diagnostics", {})
    if diag:
        lines.append("")
        lines.append("Diagnostics:")
        for key, val in diag.items():
            lines.append(f"  {key}: {'true' if val else 'false'}")
    if plan["warnings"]:
        lines.extend(["", "주의"])
        lines.extend(f"- {w}" for w in plan["warnings"])
    lines.append("")
    return "\n".join(lines) + "\n"


def build_parser() -> KoreanArgumentParser:
    parser = KoreanArgumentParser(
        description="EXPOSURE_MODE 변경 계획을 표시합니다. .env는 변경하지 않습니다."
    )
    parser.add_argument("--mode", required=True, help="대상 EXPOSURE_MODE (private_network|master_open)")
    parser.add_argument("--audience", help="EXPOSURE_AUDIENCE (local_only|private_lan|vpn|public)")
    parser.add_argument("--env", default=".env", help="점검할 env 파일. 기본값은 repository root 기준.")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        current = load_env_values(resolve_env_path(args.env))
    except RuntimeError as exc:
        print(f"env 파일 오류: {exc}", file=sys.stderr)
        return 2
    plan = build_plan(current, args.mode, args.audience)
    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    else:
        print(render_plan(plan), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
