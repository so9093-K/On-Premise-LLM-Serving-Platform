#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable

STATIC_STEPS: list[tuple[str, list[str], dict[str, str] | None]] = [
    ("계약 검증", ["__in_process__", "scripts.validation.validate_contracts"], None),
    ("exposure profiles 구조 검증", ["__in_process__", "scripts.validation.validate_exposure_profiles", "--strict"], None),
    ("compose override drift check", ["__in_process__", "scripts.compose.render_exposure_overrides", "--check"], None),
    ("env contract 검증", ["__in_process__", "scripts.validation.validate_env_contract", "--strict"], None),
    ("docs exposure semantic 검증", ["__in_process__", "scripts.validation.validate_docs_exposure"], None),
    ("런타임 검증 config-only", ["__in_process__", "scripts.validation.runtime_validation", "--config-only"], None),
    ("runtime asset drift check", ["__in_process__", "scripts.render_runtime_assets", "--check"], None),
    ("vLLM compose 정합성 검증", ["__in_process__", "scripts.compose.validate_vllm_compose"], None),
    ("OpenAPI snapshot diff", ["__in_process__", "scripts.validation.openapi_snapshot_diff"], None),
    ("runtime target projection", ["__in_process__", "scripts.reports.runtime_targets_report"], None),
    ("storage path projection", ["__in_process__", "scripts.reports.storage_paths_report"], None),
    ("project inventory projection", ["__in_process__", "scripts.reports.project_inventory_report"], None),
    ("auth profile 생성값 sanity", ["__in_process__", "scripts.auth.auth_profile_sanity"], None),
    ("auth 상태", ["__in_process__", "scripts.auth.auth_status"], None),
    ("auth doctor", ["__in_process__", "scripts.auth.auth_doctor", "--warn-only"], None),
    ("model registry 검증", ["__in_process__", "scripts.models.modelctl", "validate"], None),
    ("model registry projection diff", ["__in_process__", "scripts.models.modelctl", "diff"], None),
    ("monitoring projection", ["__in_process__", "scripts.reports.monitoring_projection_report"], None),
    ("operator status bundle", ["__in_process__", "scripts.reports.operator_status_bundle"], None),
    ("live evidence bundle", ["__in_process__", "scripts.reports.live_evidence_bundle"], None),
]

TEST_STEP = (
    "deterministic tests",
    [PYTHON, "scripts/validation/run_tests.py", "-q"],
    {"PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
)



def _timeout_handler(signum, frame) -> None:  # pragma: no cover - defensive CLI guard
    raise TimeoutError("release-check in-process step timed out")


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
        if module_name == "scripts.models.modelctl":
            result = module.main(module_args)
        elif module_name == "scripts.compose.validate_vllm_compose":
            result = module.validate_alignment()
        else:
            sys.argv = [module_name.rsplit(".", 1)[-1] + ".py", *module_args]
            result = module.main()
        return 0 if result is None else int(result)
    except TimeoutError as exc:
        raise SystemExit(f"release-check step timed out after {timeout}s: {module_name}") from exc
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        sys.argv = old_argv


def main() -> int:
    parser = argparse.ArgumentParser(description="정적 release gate와 선택적 deterministic test를 실행합니다.")
    parser.add_argument("--include-tests", action="store_true", help="scripts/validation/run_tests.py -q로 deterministic pytest도 실행합니다.")
    parser.add_argument(
        "--step-timeout-seconds",
        type=int,
        default=int(os.getenv("RELEASE_CHECK_STEP_TIMEOUT_SECONDS", "300")),
        help="각 static release step이 step label과 함께 실패하기 전까지 허용할 최대 초입니다.",
    )
    parser.add_argument(
        "--test-timeout-seconds",
        type=int,
        default=int(os.getenv("RELEASE_CHECK_TEST_TIMEOUT_SECONDS", "600")),
        help="선택 deterministic test step에 허용할 최대 초입니다.",
    )
    args = parser.parse_args()
    if args.step_timeout_seconds <= 0 or args.test_timeout_seconds <= 0:
        raise SystemExit("release-check timeouts must be positive seconds")
    env_base = os.environ.copy()
    steps = list(STATIC_STEPS)
    if args.include_tests:
        steps.append(TEST_STEP)
    for label, command, extra_env in steps:
        print(f"==> {label}", flush=True)
        env = env_base.copy()
        if extra_env:
            env.update(extra_env)
        timeout = args.test_timeout_seconds if label == TEST_STEP[0] else args.step_timeout_seconds
        if command[:1] == ["__in_process__"]:
            result = _run_in_process_step(command[1], command[2:], timeout)
            if result != 0:
                raise SystemExit(result)
            continue
        try:
            subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise SystemExit(f"release-check step timed out after {timeout}s: {label}") from exc
    print("release check 완료", flush=True)
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
