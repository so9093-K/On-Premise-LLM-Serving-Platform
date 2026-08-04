#!/usr/bin/env python3
"""`make validate`(scripts/validation/run_validate.sh)가 실제로 실행하는 오케스트레이터.

STATIC_STEPS에 나열된 개별 검증기를 순서대로 in-process로 부른다. 하나가
실패해도 나머지를 계속 실행해, 한 위반이 다른 위반을 가리지 않게 한다."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC_STEPS: list[tuple[str, list[str]]] = [
    ("계약 검증", ["__in_process__", "scripts.validation.validate_contracts"]),
    ("shell 스크립트 구문 검증", ["__in_process__", "scripts.validation.validate_shell_syntax"]),
    ("exposure profiles 구조 검증", ["__in_process__", "scripts.validation.validate_exposure_profiles", "--strict"]),
    ("compose override drift check", ["__in_process__", "scripts.compose.render_exposure_overrides", "--check"]),
    ("env contract 검증", ["__in_process__", "scripts.validation.validate_env_contract", "--strict"]),
    ("런타임 검증 config-only", ["__in_process__", "scripts.validation.runtime_validation", "--config-only"]),
    ("runtime asset drift check", ["__in_process__", "scripts.render_runtime_assets", "--check"]),
    ("OpenAPI snapshot diff", ["__in_process__", "scripts.validation.openapi_snapshot_diff"]),
    ("auth profile 생성값 sanity", ["__in_process__", "scripts.auth.auth_profile_sanity"]),
    ("auth doctor", ["__in_process__", "scripts.auth.auth_doctor", "--warn-only"]),
    ("command registry 검증", ["__in_process__", "scripts.commands.validate_command_registry", "--strict"]),
]

def _timeout_handler(signum, frame) -> None:  # pragma: no cover - defensive CLI guard
    raise TimeoutError("validate in-process step timed out")


def _run_in_process_step(module_name: str, module_args: list[str], timeout: int) -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import importlib

    old_argv = sys.argv[:]
    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    try:
        module = importlib.import_module(module_name)
        sys.argv = [module_name.rsplit(".", 1)[-1] + ".py", *module_args]
        result = module.main()
        return 0 if result is None else int(result)
    except TimeoutError as exc:
        raise SystemExit(f"validate step timed out after {timeout}s: {module_name}") from exc
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        sys.argv = old_argv


def main() -> int:
    parser = argparse.ArgumentParser(description="서비스 기동 없이 정적 검증 단계를 실행합니다.")
    parser.add_argument(
        "--step-timeout-seconds",
        type=int,
        default=300,
        help="각 정적 검증 단계가 실패하기 전까지 허용할 최대 초입니다.",
    )
    args = parser.parse_args()
    if args.step_timeout_seconds <= 0:
        raise SystemExit("validate timeout must be a positive number of seconds")
    for label, command in STATIC_STEPS:
        print(f"==> {label}", flush=True)
        if command[:1] == ["__in_process__"]:
            result = _run_in_process_step(command[1], command[2:], args.step_timeout_seconds)
            if result != 0:
                raise SystemExit(result)
            continue
    print("정적 검증 완료", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
