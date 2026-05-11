from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit('PyYAML is required: pip install pyyaml') from exc

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:
    raise SystemExit('jsonschema is required: pip install jsonschema') from exc

from .common import (
    EXPECTED_PORTS,
    FORBIDDEN_RESPONSE_FIELDS,
    REQUIRED_FILES,
    ROOT,
    iter_project_files,
    read_json,
    read_runtime_contract_text,
    read_yaml,
)

def validate_mock_scope() -> None:
    offenders = []
    for path in iter_project_files('*'):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(('tests/', 'src/ai_model_serving/governance_validation/')):
            continue
        if path.suffix.lower() not in {'.md', '.yaml', '.yml', '.json', '.py', '.sh', '.txt', '.example', '.csv', ''}:
            continue
        text = path.read_text(encoding='utf-8', errors='ignore').lower()
        if 'mock' in text:
            if rel in {'configs/retired_source_cleanup_policy.yaml', 'docs/governance/policies/retired_source_cleanup_policy.md'}:
                continue
            allowed_phrases = [
                'mock은 테스트',
                'mock is allowed only',
                'mock gateway는 금지',
                'mock detector는 `tests/`',
                'test-only mock fixtures',
                'mock은 `tests/`',
                'mock 범위를 `tests/`',
                'mock 구현은 금지',
                'mock-test-only',
                'mock-test-only 정책',
                'does not add mock monitoring services',
                'mock runtime services are not allowed outside tests',
                'mock runtime services are not allowed\n  outside tests',
                'fallback decision is not to widen mock scope',
                'mock implementations limited to `tests/` fixtures',
                'mock gateway is forbidden outside tests/ fixtures',
                'mock detector behavior is forbidden outside tests/ fixtures',
                'mock is limited to tests/ fixtures',
                'mock runtime services are not allowed outside `tests/` fixtures',
                'mock behavior is allowed only under `tests/` fixtures',
                'mock behavior is allowed only under tests/ fixtures',
                'runtime mocks remain forbidden outside',
                'mock은 tests/fixtures 전용',
                'mock은 테스트 코드/fixture 범위',
            ]
            if not any(phrase.lower() in text for phrase in allowed_phrases):
                offenders.append(rel)
    if offenders:
        raise SystemExit(f'mock references outside tests must be policy-only, found: {offenders}')

def validate_release_hygiene() -> None:
    disallowed_paths = [
        'reports/source_file_inventory.csv',
        'docs/reviews/source_file_inventory_summary.md',
        'adr/0001-origin-transition.md',
        'reports/full_project_model_feature_review_2026-05-06.md',
        'reports/project_ux_and_hardening_review_2026-05-06.md',
        'reports/operational_ux_hardening_review_0.1.6_2026-05-06.md',
        'reports/env_image_automation_review_0.1.7_2026-05-06.md',
    ]
    present = [p for p in disallowed_paths if (ROOT / p).exists()]
    if present:
        raise SystemExit(f'unnecessary detailed retired-source artifacts must not be packaged: {present}')

    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    required_excludes = [
        '.env.*', 'model_cache/*', 'models/*', 'logs/*', 'dist/*', '**/*.pyc',
        'reports/runtime/runtime_validation_*.json', 'reports/runtime/runtime_validation_*.md',
    ]
    for pattern in required_excludes:
        if pattern not in package_script:
            raise SystemExit(f'package_release.sh missing hygiene exclude: {pattern}')

def validate_pytest_stability_config() -> None:
    pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    addopts = pyproject['tool']['pytest']['ini_options'].get('addopts', '')
    for plugin in ['cacheprovider', 'ddtrace', 'asyncio']:
        if f'-p no:{plugin}' not in addopts:
            raise SystemExit(f'pytest addopts must disable external plugin: {plugin}')

    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    if 'PYTHONDONTWRITEBYTECODE=1' not in makefile:
        raise SystemExit('Makefile test target must avoid writing Python bytecode caches')
    if 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1' not in makefile:
        raise SystemExit('Makefile test target must disable external pytest plugin autoload')

def validate_runtime_validation_harness() -> None:
    for rel in ['scripts/compose/preflight_compose.sh',
    'scripts/compose/compose_diagnostics.sh',
    'scripts/compose/validate_vllm_compose.py', 'scripts/models/render_vllm_commands.py']:
        path = ROOT / rel
        if not path.exists():
            raise SystemExit(f'runtime validation harness missing: {rel}')
        if not os.access(path, os.X_OK):
            raise SystemExit(f'runtime validation harness must be executable: {rel}')

    runtime_script = (ROOT / 'scripts/validation/runtime_validation.py').read_text(encoding='utf-8')
    for phrase in [
        'reports/runtime',
        'nvidia-smi',
        '/api/v1/targets',
        'FORBIDDEN_RISK_FIELDS',
        'Raw prompts, user text, and model output are not written',
        '--config-only',
    ]:
        if phrase not in runtime_script:
            raise SystemExit(f'runtime_validation.py missing required behavior marker: {phrase}')

    render_script = (ROOT / 'scripts/models/render_vllm_commands.py').read_text(encoding='utf-8')
    for phrase in ['render_vllm_command', 'configs/model_serving.yaml', '--service']:
        if phrase not in render_script:
            raise SystemExit(f'render_vllm_commands.py missing required behavior marker: {phrase}')

    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    for target in ['runtime-validate:', 'vllm-commands:', 'live-evidence:', 'release-check:']:
        if target not in makefile:
            raise SystemExit(f'Makefile missing P2 harness target: {target}')

    plan = (ROOT / 'harness/runtime_validation_plan.md').read_text(encoding='utf-8')
    for phrase in ['Runtime Validation Plan', 'Validation Report Rule', 'reports/runtime/runtime_validation_', 'does not record raw prompts']:
        if phrase not in plan:
            raise SystemExit(f'runtime validation plan missing phrase: {phrase}')

def validate_implementation_reference() -> None:
    src_files = [
        'src/ai_model_serving/apps/gateway.py',
        'src/ai_model_serving/apps/risk_adapter.py',
        'src/ai_model_serving/settings.py',
    'src/ai_model_serving/status.py',
    'src/ai_model_serving/domain/model_registry.py',
        'src/ai_model_serving/upstream.py',
        'src/ai_model_serving/risk.py',
    ]
    for rel in src_files:
        path = ROOT / rel
        if not path.exists():
            raise SystemExit(f'platform implementation source file missing: {rel}')
        text = path.read_text(encoding='utf-8')
        if 'FastAPI' not in text and rel.endswith(('gateway.py', 'risk_adapter.py')):
            raise SystemExit(f'platform implementation app must create a FastAPI service: {rel}')
    pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    dependencies = '\n'.join(pyproject['project'].get('dependencies', []))
    for dep in ['fastapi', 'uvicorn', 'httpx', 'prometheus-client', 'PyYAML']:
        if dep not in dependencies:
            raise SystemExit(f'pyproject missing runtime dependency: {dep}')
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    if 'scripts/validation/run_tests.py' not in makefile:
        raise SystemExit('make test must run unit and contract tests through deterministic run_tests.py')
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    docs = (ROOT / 'README.md').read_text(encoding='utf-8')
    for phrase in [version, 'Gateway', 'Risk Adapter', 'runtime validation report']:
        if phrase not in docs:
            raise SystemExit(f'platform implementation docs missing phrase: {phrase}')

def validate_deployment_reproducibility() -> None:
    compat = read_yaml('configs/runtime_compatibility.yaml')
    if compat['package_tools'].get('lockfile_required_for_runtime_phase') is not True:
        raise SystemExit('runtime_compatibility must require a runtime lockfile')
    lock = ROOT / 'requirements.lock'
    lock_text = lock.read_text(encoding='utf-8') if lock.exists() else ''
    for required_pin in ['fastapi==', 'uvicorn==', 'httpx==', 'PyYAML==', 'pytest==8.', 'jsonschema==']:
        if required_pin not in lock_text:
            raise SystemExit(f'requirements.lock missing required dependency pin: {required_pin}')
    if re.search(r'(?m)^pytest==9\.', lock_text):
        raise SystemExit('requirements.lock pytest pin violates pyproject pytest<9 constraint')

    pyproject_text = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    for required_pin in ['fastapi==0.128.2', 'anyio==4.9.0', 'uvicorn[standard]==0.46.0', 'httpx==0.28.1', 'pytest==8.4.2', 'jsonschema==4.26.0']:
        if required_pin not in pyproject_text:
            raise SystemExit(f'pyproject.toml must pin validated dependency: {required_pin}')

    runtime_lock = ROOT / 'requirements.runtime.lock'
    runtime_lock_text = runtime_lock.read_text(encoding='utf-8') if runtime_lock.exists() else ''
    for required_pin in ['fastapi==', 'uvicorn==', 'httpx==', 'PyYAML==']:
        if required_pin not in runtime_lock_text:
            raise SystemExit(f'requirements.runtime.lock missing runtime dependency pin: {required_pin}')
    for forbidden_pin in ['pytest==', 'jsonschema==']:
        if re.search(rf'(?m)^{re.escape(forbidden_pin)}', runtime_lock_text):
            raise SystemExit(f'requirements.runtime.lock must not include contract/test dependency pin: {forbidden_pin}')

    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    for phrase in [
        'FROM python:3.12.13-slim',
        'requirements.runtime.lock',
        '--requirement requirements.runtime.lock',
        '--no-deps .',
        'useradd',
        'USER appuser',
        'HEALTHCHECK',
        'HEALTHCHECK_PORT',
        '/health',
    ]:
        if phrase not in dockerfile:
            raise SystemExit(f'Dockerfile missing deployment hardening: {phrase}')

    bootstrap = (ROOT / 'scripts/build/bootstrap.sh').read_text(encoding='utf-8')
    for phrase in ['--requirement requirements.lock', '--no-deps -e ".[contract]"']:
        if phrase not in bootstrap:
            raise SystemExit(f'bootstrap.sh must install from lockfile before editable package install: {phrase}')
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    if 'PYTHON ?= $(if $(PYTHON_BIN)' not in makefile:
        raise SystemExit('Makefile must honor caller-provided PYTHON_BIN for bootstrap/CI validation.')

    compose = read_yaml('ops/compose/full-stack.example.yaml')
    for service, expected_port in {'gateway': '9400', 'risk-adapter': '9405'}.items():
        env = compose['services'][service].get('environment', {})
        if str(env.get('HEALTHCHECK_PORT')) != expected_port:
            raise SystemExit(f'{service} compose service must set HEALTHCHECK_PORT={expected_port}')

    release_checklist = (ROOT / 'docs/release/release_checklist.md').read_text(encoding='utf-8')
    for phrase in ['make runtime-targets', 'make monitoring-projection', 'make operator-status', 'make runtime-validate', 'make package']:
        if phrase not in release_checklist:
            raise SystemExit(f'release checklist missing required gate: {phrase}')

    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    for safe_example in ['.env.example', '.env.local.example', '.env.compose.example']:
        if f'"$BASE/{safe_example}"' not in package_script:
            raise SystemExit(f'package_release.sh must explicitly include safe env example {safe_example}')
    for pattern in ['reports/runtime/runtime_validation_*.json', 'reports/runtime/runtime_validation_*.md']:
        if pattern not in package_script:
            raise SystemExit(f'package_release.sh must exclude generated runtime reports: {pattern}')
    if 'date_time = _EPOCH' not in package_script:
        raise SystemExit('package_release.sh must normalize ZIP timestamps for reproducible builds')

    for ignore_file in ['.gitignore', '.dockerignore']:
        ignore_text = (ROOT / ignore_file).read_text(encoding='utf-8')
        for safe_example in ['!.env.example', '!.env.local.example', '!.env.compose.example']:
            if safe_example not in ignore_text:
                raise SystemExit(f'{ignore_file} must keep safe env examples visible: {safe_example}')

def validate_vllm_compose_contract() -> None:
    import subprocess
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/compose/validate_vllm_compose.py')],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise SystemExit(output or 'vLLM compose validation failed')

def validate_risk_vllm_patch_lifecycle() -> None:
    dockerfile = (ROOT / 'ops/docker/Dockerfile.risk-vllm-kanana').read_text(encoding='utf-8')
    patch_script = (ROOT / 'ops/patches/transformers_llama_head_dim_guard.py').read_text(encoding='utf-8')
    patch_doc = (ROOT / 'ops/patches/README.md').read_text(encoding='utf-8')
    lifecycle_doc = (ROOT / 'docs/operations/risk_vllm_patch_lifecycle.md').read_text(encoding='utf-8')
    build_script = (ROOT / 'scripts/build/build_risk_vllm_image.sh').read_text(encoding='utf-8')
    check_script = (ROOT / 'scripts/models/check_risk_vllm_image_config.sh').read_text(encoding='utf-8')
    dockerignore = (ROOT / '.dockerignore').read_text(encoding='utf-8')

    if 'RUN python3 - <<' in dockerfile and 'src.replace' in dockerfile:
        raise SystemExit('risk vLLM Dockerfile must not keep inline site-packages patch logic')
    for phrase in [
        'COPY ops/patches/transformers_llama_head_dim_guard.py',
        'transformers_llama_head_dim_guard.py --json',
        'transformers_llama_head_dim_guard.py --verify --json',
        'ai_model_serving.patch.transformers_llama_head_dim_guard="true"',
        'ai_model_serving.patch.remove_when',
        'TRANSFORMERS_MIN_VERSION',
    ]:
        if phrase not in dockerfile:
            raise SystemExit(f'risk vLLM Dockerfile missing patch lifecycle phrase: {phrase}')
    for phrase in [
        'PATCH_ID = "transformers_llama_head_dim_guard"',
        'DEFAULT_METADATA_PATH',
        'original_file_sha256',
        'patched_file_sha256',
        '--verify',
        'remove_when',
    ]:
        if phrase not in patch_script:
            raise SystemExit(f'patch script missing lifecycle phrase: {phrase}')
    for phrase in [
        'RISK_VLLM_TRANSFORMERS_MIN_VERSION',
        'RISK_VLLM_TRANSFORMERS_VERSION',
        '--build-arg "TRANSFORMERS_MIN_VERSION=',
    ]:
        if phrase not in build_script:
            raise SystemExit(f'build_risk_vllm_image.sh missing compatibility phrase: {phrase}')
    for phrase in [
        'ops/*',
        '!ops/docker/Dockerfile.risk-vllm-kanana',
        '!ops/patches/transformers_llama_head_dim_guard.py',
    ]:
        if phrase not in dockerignore:
            raise SystemExit(f'.dockerignore must allow risk vLLM build context file: {phrase}')
    for phrase in [
        'ai_model_serving.patch.transformers_llama_head_dim_guard',
        'transformers_llama_head_dim_guard.py',
        'SKIP_RISK_VLLM_PATCH_VERIFY',
    ]:
        if phrase not in check_script:
            raise SystemExit(f'check_risk_vllm_image_config.sh missing patch verification phrase: {phrase}')
    for phrase in ['Patch lifecycle', 'metadata', 'removal condition']:
        if phrase.lower() not in (patch_doc + '\n' + lifecycle_doc).lower():
            raise SystemExit(f'risk vLLM patch docs missing phrase: {phrase}')
