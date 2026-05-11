#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.lib.cli_kr import KoreanArgumentParser  # noqa: E402
from ai_model_serving.auth_control import diagnose_auth, render_auth_findings
from ai_model_serving.settings import ROOT as SETTINGS_ROOT, load_settings


def _env_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else SETTINGS_ROOT / path


def main() -> int:
    parser = KoreanArgumentParser(description="인증 설정 위험과 profile drift를 진단합니다.")
    parser.add_argument("--json", action="store_true", help="기계가 읽기 쉬운 JSON을 출력합니다.")
    parser.add_argument("--warn-only", action="store_true", help="FAIL finding이 있어도 exit code 0으로 종료합니다.")
    parser.add_argument("--env", help="진단할 env 파일 경로입니다. 기본값은 repository root의 .env입니다.")
    args = parser.parse_args()
    env_path = _env_path(args.env)
    if env_path is not None and not env_path.exists():
        print(f"env 파일을 찾을 수 없습니다: {env_path}", file=sys.stderr)
        return 2
    settings = load_settings(env_file=env_path)
    findings = diagnose_auth(settings, SETTINGS_ROOT)
    if args.json:
        print(json.dumps([finding.as_dict() for finding in findings], ensure_ascii=False, indent=2))
    else:
        print(render_auth_findings(findings), end="")
    has_fail = any(finding.level == "FAIL" for finding in findings)
    return 0 if args.warn_only or not has_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
