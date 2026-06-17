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
    FORBIDDEN_RESPONSE_FIELDS,
    REQUIRED_FILES,
    ROOT,
    iter_project_files,
    read_json,
    read_runtime_contract_text,
    read_yaml,
    service_default_host_ports,
)

def validate_doc_invariants() -> None:
    api_doc = (ROOT / 'docs/specs/api.md').read_text(encoding='utf-8')
    risk_doc = (ROOT / 'docs/specs/risk_signal_contract.md').read_text(encoding='utf-8')
    combined = api_doc + '\n' + risk_doc
    for field in FORBIDDEN_RESPONSE_FIELDS:
        if field not in combined:
            raise SystemExit(f'forbidden field missing from docs: {field}')
    decision_register = (ROOT / 'docs/02_decision_register.md').read_text(encoding='utf-8')
    source_review = (ROOT / 'docs/01_project_background.md').read_text(encoding='utf-8')
    if 'D-001' not in decision_register or '과거 원천 프로젝트 코드는 포함하지 않는다' not in decision_register:
        raise SystemExit('D-001 must preserve the retired-source exclusion decision after ADR-0001 removal')
    if 'ADR-0001' not in source_review or '별도 파일로 유지하지 않는다' not in source_review:
        raise SystemExit('project background must explain ADR-0001 cleanup decision')
    day0 = (ROOT / 'docs/operations/day0_quickstart.md').read_text(encoding='utf-8')
    if '.env.infisical.example' in day0 or '.env.infisical' in day0:
        raise SystemExit('day0 quickstart must use generated .env for Infisical, not stale Infisical-only env files')
    for phrase in [
        '## 6. 시크릿 관리 Infisical (선택)',
        'make init-env-compose',
        'INFISICAL_AUTH_SECRET / INFISICAL_ENCRYPTION_KEY 는 여기서 자동 생성된다',
        '## 7. Runtime secret directory와 테스트',
    ]:
        if phrase not in day0:
            raise SystemExit(f'day0 quickstart missing current Infisical/runtime-secret phrase: {phrase}')
    for rel in ['reports/refactor/current_refactor_state.md', 'reports/refactor/current_handoff_summary.md']:
        if not (ROOT / rel).exists():
            raise SystemExit(f'current handoff entrypoint missing: {rel}')
    stale_reference_patterns = {
        'dated current handoff path': re.compile(r'current_refactor_state_\d{4}-\d{2}-\d{2}\.md'),
        'dated project inventory path': re.compile(r'project_inventory_phase\d+_\d{4}-\d{2}-\d{2}\.(?:csv|json|md)'),
        'phase summary handoff report': re.compile(r'reports/refactor/phase\d+_(?:summary|validation|documentation_consistency|integrated_management).*\.(?:md|json|txt)'),
    }
    stale_refs: list[str] = []
    for path in iter_project_files():
        rel = path.relative_to(ROOT).as_posix()
        if path.suffix not in {'.md', '.py', '.yaml', '.yml', '.json', '.sh'}:
            continue
        text = path.read_text(encoding='utf-8')
        for label, pattern in stale_reference_patterns.items():
            if pattern.search(text):
                stale_refs.append(f'{rel}: {label}')
                break
    if stale_refs:
        raise SystemExit(f'stable current handoff/project-inventory paths regressed: {stale_refs}')

def validate_monitoring_reference() -> None:
    monitoring = read_yaml('configs/monitoring.yaml')
    ports = service_default_host_ports()
    for name in ('prometheus', 'grafana', 'dcgm_exporter'):
        port = monitoring['monitoring_stack'][name]['port']
        if ports.get(name) != port:
            raise SystemExit(f'monitoring port mismatch: {name} services.yaml={ports.get(name)} monitoring.yaml={port}')
    dashboards = {d['id'] for d in monitoring['ux_dashboards']}
    required = {
        'serving_home',
        'gpu_capacity_and_oom_risk',
        'risk_signal_operations',
        'api_experience',
        'model_runtime_deep_dive',
        'observability_data_quality',
    }
    if not required.issubset(dashboards):
        raise SystemExit(f'missing required monitoring dashboards: {required - dashboards}')
    forbidden_text_labels = monitoring['privacy_and_security']
    for key in ['raw_prompt_in_metrics', 'user_text_labels', 'model_output_text_labels']:
        if forbidden_text_labels.get(key) != 'forbidden':
            raise SystemExit(f'monitoring privacy policy must forbid {key}')

    operator = monitoring.get('operator_status_ux', {})
    if operator.get('landing_dashboard') != 'gpu_capacity_and_oom_risk':
        raise SystemExit('monitoring UX must define gpu_capacity_and_oom_risk as the landing dashboard')
    levels = {item['level'] for item in operator.get('status_levels', [])}
    if levels != {'green', 'yellow', 'red', 'gray'}:
        raise SystemExit(f'monitoring status levels must be green/yellow/red/gray, got {levels}')
    first_screen = operator.get('first_screen_order', [])
    for expected in ['overall_status', 'model_gateway_ready', 'prometheus_target_health', 'gpu_headroom']:
        if expected not in first_screen:
            raise SystemExit(f'monitoring first screen must include {expected}')

    from ai_model_serving.domain import ModelRegistry
    from ai_model_serving.monitoring_projection import monitoring_projection_document, prometheus_scrape_config_document

    registry = ModelRegistry(read_yaml('configs/model_catalog.yaml'), read_yaml('configs/model_serving.yaml'))
    projected_prometheus = prometheus_scrape_config_document(registry=registry, monitoring=monitoring)
    actual_prometheus = read_yaml('ops/prometheus/prometheus.yml')
    if actual_prometheus != projected_prometheus:
        raise SystemExit('ops/prometheus/prometheus.yml must match registry monitoring projection')
    monitoring_projection = monitoring_projection_document(registry=registry, monitoring=monitoring)
    if monitoring_projection['privacy_contract'] != {
        'raw_prompt_included': False,
        'user_text_included': False,
        'model_output_included': False,
        'authorization_header_included': False,
    }:
        raise SystemExit('monitoring projection must not include prompt/user/model-output/auth data')
    resource_monitoring = monitoring.get('resource_monitoring', {})
    if tuple(resource_monitoring.get('model_labels', [])) != registry.monitoring_model_labels():
        raise SystemExit('monitoring resource model_labels must match ModelRegistry monitoring model labels')

    grafana = monitoring.get('monitoring_stack', {}).get('grafana', {})
    if grafana.get('provisioned_datasource_uid') != 'prometheus':
        raise SystemExit('grafana provisioning must define the portable prometheus datasource uid')
    if grafana.get('allow_ui_updates_policy', {}).get('reference_release') is not False:
        raise SystemExit('reference release dashboards must be Git-managed with allowUiUpdates=false')
    if not {'datasource', 'window', 'user_route'}.issubset(set(grafana.get('dashboard_variables', []))):
        raise SystemExit('grafana monitoring config missing required dashboard variables')

    doc = (ROOT / 'docs/operations/monitoring_ux.md').read_text(encoding='utf-8')
    for phrase in ['Prometheus', 'Grafana', 'No prompt leakage', 'Risk Signal Activity', 'No Runtime Data', '한글 우선', '$datasource', 'streaming_time_to_first_chunk_seconds_bucket']:
        if phrase not in doc:
            raise SystemExit(f'monitoring UX doc missing phrase: {phrase}')
    endpoint_doc = (ROOT / 'docs/operations/endpoint_reference.md').read_text(encoding='utf-8')
    for dashboard_id in sorted(required):
        if f'`{dashboard_id}`' not in endpoint_doc:
            raise SystemExit(f'endpoint reference missing Grafana dashboard uid: {dashboard_id}')
    if 'clamp_min(sum(rate(http_requests_total' in endpoint_doc:
        raise SystemExit('endpoint reference must not document low-traffic-skewed rate/clamp error ratio')
    if 'allowUiUpdates=false' not in endpoint_doc:
        raise SystemExit('endpoint reference must document Git-managed Grafana allowUiUpdates=false policy')
    status_doc = (ROOT / 'docs/operations/grafana_status_board.md').read_text(encoding='utf-8')
    for phrase in ['지금 요청을 안전하게 처리할 수 있는가?', 'Serving State', 'Action Required', 'No Runtime Data', 'make operator-status', 'operator_status_bundle.json']:
        if phrase not in status_doc:
            raise SystemExit(f'status board UX doc missing phrase: {phrase}')

def validate_status_vocabulary() -> None:
    from ai_model_serving import status as status_vocab

    readiness_schema = read_json('specs/schemas/readiness_response.schema.json')
    schema_statuses = tuple(readiness_schema['properties']['status']['enum'])
    dependency_statuses = tuple(readiness_schema['properties']['dependencies']['items']['properties']['status']['enum'])
    phases = tuple(readiness_schema['properties']['phase']['enum'])
    if schema_statuses != status_vocab.READINESS_STATUSES:
        raise SystemExit('readiness response status enum must match ai_model_serving.status.READINESS_STATUSES')
    if dependency_statuses != status_vocab.DEPENDENCY_STATUSES:
        raise SystemExit('readiness dependency status enum must match ai_model_serving.status.DEPENDENCY_STATUSES')
    if phases != status_vocab.READINESS_PHASES:
        raise SystemExit('readiness phase enum must match ai_model_serving.status.READINESS_PHASES')
    readiness_text = (ROOT / 'src/ai_model_serving/services/readiness.py').read_text(encoding='utf-8')
    for phrase in ['overall_readiness', 'readiness_phase', 'READY', 'NOT_READY']:
        if phrase not in readiness_text:
            raise SystemExit(f'readiness service must use shared status vocabulary: {phrase}')

def validate_ops_templates() -> None:
    from ai_model_serving.domain import ModelRegistry

    registry = ModelRegistry(read_yaml('configs/model_catalog.yaml'), read_yaml('configs/model_serving.yaml'))
    from ai_model_serving.monitoring_projection import prometheus_scrape_config_document

    prom = read_yaml('ops/prometheus/prometheus.yml')
    jobs = {job['job_name'] for job in prom['scrape_configs']}
    required_jobs = {'gateway', 'risk-adapter', 'vllm-runtimes', 'dcgm-exporter', 'cadvisor'}
    if not required_jobs.issubset(jobs):
        raise SystemExit(f'prometheus template missing jobs: {required_jobs - jobs}')
    vllm_job = next(job for job in prom['scrape_configs'] if job['job_name'] == 'vllm-runtimes')
    labels = {
        cfg['labels']['model']: cfg['labels'].get('runtime_service')
        for cfg in vllm_job.get('static_configs', [])
        if 'labels' in cfg
    }
    expected_labels = {target.logical_id: target.compose_service_name for target in registry.monitoring_targets()}
    if labels != expected_labels:
        raise SystemExit(f'vLLM scrape labels must use logical model ids and runtime services: {labels}')
    rules = (ROOT / 'ops/prometheus/rules/model_runtime.rules.yml').read_text(encoding='utf-8')
    for record in ['vllm_container_memory_usage_bytes', 'vllm_container_cpu_cores_used', 'container_label_com_docker_compose_service']:
        if record not in rules:
            raise SystemExit(f'prometheus recording rules missing per-container resource bridge: {record}')
    if registry.monitoring_compose_service_regex() not in rules:
        raise SystemExit('prometheus recording rules must use ModelRegistry compose service label regex')
    from ai_model_serving.governance_validation.monitoring_dashboards import validate_grafana_dashboard_templates

    validate_grafana_dashboard_templates()

def validate_runtime_validation_yaml() -> None:
    from ai_model_serving.domain import ModelRegistry

    matrix = read_yaml('harness/runtime_validation_matrix.yaml')
    registry = ModelRegistry(read_yaml('configs/model_catalog.yaml'), read_yaml('configs/model_serving.yaml'))
    expected_matrix = registry.runtime_validation_matrix_document()
    if matrix.get('validation_policy') != 'runtime_validation_required':
        raise SystemExit('runtime validation matrix must require runtime validation')
    checks = {check['id']: check for check in matrix['validation_checks']}
    expected_checks = {check['id']: check for check in expected_matrix['validation_checks']}
    required = {'gateway-runtime', 'risk-adapter-runtime', 'vllm-runtime', 'gpu-capacity', 'monitoring-scrape', 'grafana-dashboard-render'}
    missing = required - set(checks)
    if missing:
        raise SystemExit(f'runtime validation matrix missing checks: {missing}')
    if set(checks) != set(expected_checks):
        raise SystemExit(f'runtime validation matrix ids must match ModelRegistry projection: matrix={set(checks)}, registry={set(expected_checks)}')
    for check_id, check in checks.items():
        for field in ['owner', 'validation', 'artifact_file', 'operator_action', 'runtime_validation_required']:
            if field not in check:
                raise SystemExit(f'runtime validation check missing {field}: {check.get("id")}')
        if not str(check['artifact_file']).startswith('reports/runtime/'):
            raise SystemExit(f'runtime validation report path must point to reports/runtime/: {check.get("id")}')
        if check['runtime_validation_required'] is not True:
            raise SystemExit(f'runtime validation check must require runtime validation: {check.get("id")}')
        expected = expected_checks[check_id]
        for projected_field in ['models', 'runtime_services']:
            if sorted(check.get(projected_field, [])) != sorted(expected.get(projected_field, [])):
                raise SystemExit(f'runtime validation matrix {check_id}.{projected_field} must match ModelRegistry projection')

def validate_health_readiness_contract_docs() -> None:
    api_doc = (ROOT / 'docs/specs/api.md').read_text(encoding='utf-8')
    if 'Health/readiness 노출 제약' not in api_doc or 'internal network' not in api_doc:
        raise SystemExit('API docs must define health/readiness exposure constraints')
    for path in ['specs/openapi.gateway.yaml', 'specs/openapi.risk-adapter.yaml']:
        doc = read_yaml(path)
        ready = doc['paths']['/ready']['get']
        ready_description = ready.get('description', '')
        if not any(phrase in ready_description for phrase in ['내부 readiness endpoint', 'Internal readiness endpoint']):
            raise SystemExit(f'{path} must describe /ready as internal readiness endpoint')

def validate_api_contract_matrix() -> None:
    with (ROOT / 'contracts/api_contract_matrix.csv').open(encoding='utf-8', newline='') as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit('api_contract_matrix.csv must not be empty')
    required_columns = {'auth_required', 'admin_auth', 'exposure', 'request_schema', 'response_schema'}
    missing_columns = required_columns - set(rows[0])
    if missing_columns:
        raise SystemExit(f'api_contract_matrix.csv missing columns: {missing_columns}')
    schemas_dir = ROOT / 'specs' / 'schemas'
    valid_admin_auth = {'none', 'conditional(ADMIN_API_KEY_REQUIRED)'}
    for row in rows:
        if row['policy_action'] != 'not defined':
            raise SystemExit(f'policy action must remain undefined in API contract matrix: {row}')
        if row['auth_required'] not in {'true', 'false'}:
            raise SystemExit(f'auth_required must be true/false: {row}')
        if row['admin_auth'] not in valid_admin_auth:
            raise SystemExit(f'admin_auth must be one of {valid_admin_auth}: {row}')
        if row['admin_auth'] == 'conditional(ADMIN_API_KEY_REQUIRED)' and row['auth_required'] != 'false':
            raise SystemExit(f'conditional admin_auth endpoints must have auth_required=false (bearer auth is separate): {row}')
        for key in ['request_schema', 'response_schema']:
            value = row[key]
            if value != 'none' and not (schemas_dir / value).exists():
                raise SystemExit(f'api contract matrix references missing schema {value}: {row}')
    ready_rows = [row for row in rows if row['public_endpoint'] == '/ready']
    if not ready_rows or ready_rows[0]['exposure'] != 'internal_only':
        raise SystemExit('/ready must be marked internal_only in api_contract_matrix.csv')

def validate_retired_source_cleanup_policy() -> None:
    policy_doc = (ROOT / 'docs/governance/policies/retired_source_cleanup_policy.md').read_text(encoding='utf-8')
    policy = read_yaml('configs/retired_source_cleanup_policy.yaml')
    if policy.get('policy_name') != 'retired_source_cleanup_policy':
        raise SystemExit('retired-source cleanup policy config missing policy_name')
    for phrase in ['Retired Source Cleanup Policy', 'ADR-0001', 'D-001', 'tests/']:
        if phrase not in policy_doc:
            raise SystemExit(f'retired-source cleanup policy doc missing phrase: {phrase}')
    prohibited = set(policy.get('prohibited_release_paths', []))
    for required in [
        'reports/source_file_inventory.csv',
        'docs/reviews/source_file_inventory_summary.md',
        'reports/full_project_model_feature_review_2026-05-06.md',
        'reports/project_ux_and_hardening_review_2026-05-06.md',
        'reports/operational_ux_hardening_review_0.1.6_2026-05-06.md',
        'reports/env_image_automation_review_0.1.7_2026-05-06.md',
        'legacy/',
    ]:
        if required not in prohibited:
            raise SystemExit(f'retired-source cleanup policy missing prohibited path: {required}')
    if policy.get('mock_policy', {}).get('runtime_mock_allowed') is not False:
        raise SystemExit('retired-source cleanup policy must forbid runtime mocks')
    for rel in policy.get('prohibited_release_paths', []):
        if rel.endswith('/'):
            if (ROOT / rel).exists():
                raise SystemExit(f'prohibited retired-source directory exists: {rel}')
        elif (ROOT / rel).exists():
            raise SystemExit(f'prohibited retired-source file exists: {rel}')
    for pattern in policy.get('prohibited_refactor_report_patterns', []):
        matches = sorted(ROOT.glob(pattern))
        if matches:
            rel_matches = [str(path.relative_to(ROOT)) for path in matches]
            raise SystemExit(f'prohibited stale refactor reports exist for {pattern}: {rel_matches}')

def _validate_operator_workflow_targets_in_registry(root: Path) -> None:
    try:
        import yaml as _yaml
    except ImportError:
        return
    rpath = root / 'configs' / 'command_registry.yaml'
    if not rpath.exists():
        raise SystemExit('configs/command_registry.yaml 이 없습니다')
    registered = {c['make_target'] for c in _yaml.safe_load(rpath.read_text(encoding='utf-8')).get('commands', [])}
    required = ['guide', 'runtime-targets', 'storage-paths', 'project-inventory', 'monitoring-projection', 'operator-status', 'operator-reports', 'live-evidence', 'release-check', 'release-check-full', 'cleanup-plan', 'remove-plan', 'build-pipeline', 'first-run', 'rebuild-full']
    for t in required:
        if t not in registered:
            raise SystemExit(f'command_registry.yaml에 operator workflow target 누락: {t}')


def validate_build_ux_roles() -> None:
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    for target in ['guide:', 'start:', 'up:', 'ready:', 'check-ready:', 'runtime-targets:', 'storage-paths:', 'project-inventory:', 'monitoring-projection:', 'operator-status:', 'operator-reports:', 'live-evidence:', 'release-check:', 'release-check-full:', 'status:', 'stop:', 'down:', 'logs:', 'cleanup-plan:', 'remove-plan:', 'build-pipeline:', 'first-run:', 'rebuild-full:', 'rebuild-app:', 'rebuild-risk-vllm:']:
        if target not in makefile:
            raise SystemExit(f'Makefile missing runtime UX target: {target}')
    if 'scripts/validation/run_tests.py' not in makefile:
        raise SystemExit('Makefile test target must use deterministic run_tests.py')
    if 'make build         # build artifacts/images only; does not start or keep services alive' not in makefile:
        raise SystemExit('Makefile help must state that make build does not start services')
    _validate_operator_workflow_targets_in_registry(ROOT)
    for script in ['scripts/validation/run_tests.py', 'scripts/reports/operator_guide.py', 'scripts/reports/operator_status_bundle.py', 'scripts/reports/live_evidence_bundle.py', 'scripts/validation/release_check.py', 'scripts/reports/monitoring_projection_report.py', 'scripts/reports/runtime_targets_report.py', 'scripts/reports/storage_paths_report.py', 'scripts/reports/project_inventory_report.py',
    'scripts/ops/start_services.sh', 'scripts/ops/up_services.sh', 'scripts/ops/stop_services.sh', 'scripts/ops/down_services.sh', 'scripts/ops/status_services.sh', 'scripts/ops/ready_check.sh']:
        path = ROOT / script
        if not path.exists():
            raise SystemExit(f'missing runtime UX script: {script}')
        if not os.access(path, os.X_OK):
            raise SystemExit(f'runtime UX script must be executable: {script}')
    build_doc = (ROOT / 'docs/development/build_ux.md').read_text(encoding='utf-8')
    for phrase in ['build, start, readiness, deploy, release는 서로 다른 동작이다.', '`make build`는 artifact/image를 생성하고 검증한다.', '`make start`는 local service 또는 compose stack을 시작한다.', '`make ready`는 live stack readiness를 증명한다.']:
        if phrase not in build_doc:
            raise SystemExit(f'build UX doc missing role phrase: {phrase}')
    scripts_doc = (ROOT / 'scripts/README.md').read_text(encoding='utf-8')
    for phrase in ['build와 runtime은 다른 단계다', '`make build`는 서비스를 시작하지 않는다', 'make start', 'make ready', 'make guide', 'make storage-paths', 'make project-inventory', 'make monitoring-projection', 'make operator-status', 'make operator-reports', 'make live-evidence', 'make release-check', 'make cleanup-plan', 'make remove-plan', 'make build-pipeline', 'make first-run', 'make rebuild-full']:
        if phrase not in scripts_doc:
            raise SystemExit(f'scripts README missing build/runtime UX phrase: {phrase}')
    for doc_path, phrase in {
        'docs/operations/operator_workflows.md': 'make operator-reports',
        'docs/operations/first_project_guide.md': '처음 프로젝트를 받았을 때 전체 가이드',
        'docs/operations/configuration_lifecycle.md': '통합 설정·관리·빌드·제거 UX',
        'docs/operations/storage_paths.md': 'configs/storage_paths.yaml',
        'docs/operations/project_management_workflow.md': 'make project-inventory',
    }.items():
        if phrase not in (ROOT / doc_path).read_text(encoding='utf-8'):
            raise SystemExit(f'{doc_path} missing UX guide phrase: {phrase}')
    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    if '"$BASE/run/*"' not in package_script:
        raise SystemExit('package_release.sh must exclude run/ service pid artifacts')
    if "'models'," in package_script.split('exclude_tree_dirs = {', 1)[1].split('}', 1)[0]:
        raise SystemExit('package_release.sh must not exclude every directory named models; docs/models is required')
    clean_script = (ROOT / 'scripts/ops/clean_all.sh').read_text(encoding='utf-8')
    if '"$ROOT/run"' not in clean_script:
        raise SystemExit('clean_all.sh must remove run/ pid artifacts')
    if '"$ROOT/ops/compose/model_cache"' not in clean_script:
        raise SystemExit('clean_all.sh must remove legacy compose-relative model cache when PURGE_MODEL_CACHE=1')

def validate_command_terminology_policy() -> None:
    policy_doc = (ROOT / 'docs/governance/policies/command_terminology_policy.md').read_text(encoding='utf-8')
    policy = read_yaml('configs/command_terminology_policy.yaml')
    if policy.get('policy_name') != 'command_terminology_policy':
        raise SystemExit('command terminology policy config missing policy_name')
    if policy.get('principle') != 'standard_command_semantics':
        raise SystemExit('command terminology policy must define standard command semantics')

    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    commands = policy.get('canonical_commands', {})
    for command, spec in commands.items():
        if spec.get('make_target_required') is True and f'{command}:' not in makefile:
            raise SystemExit(f'Makefile missing canonical command target: {command}')
        if command == 'build' and spec.get('starts_services') is not False:
            raise SystemExit('build command must not start services')
        if command == 'start' and spec.get('starts_services') is not True:
            raise SystemExit('start command must be the service-starting command')

    aliases = policy.get('aliases', {})
    for alias, spec in aliases.items():
        if spec.get('make_target_required') is True and f'{alias}:' not in makefile:
            raise SystemExit(f'Makefile missing command alias target: {alias}')
        canonical = spec.get('canonical')
        if canonical not in commands:
            raise SystemExit(f'command alias points to unknown canonical command: {alias}')

    for phrase in policy.get('required_documentation_phrases', []):
        haystack = '\n'.join([
            policy_doc,
            (ROOT / 'docs/development/build_ux.md').read_text(encoding='utf-8'),
            (ROOT / 'scripts/README.md').read_text(encoding='utf-8'),
            (ROOT / 'README.md').read_text(encoding='utf-8'),
        ])
        if phrase not in haystack:
            raise SystemExit(f'command terminology docs missing phrase: {phrase}')
    for phrase in policy.get('forbidden_phrases', []):
        haystack = '\n'.join([
            makefile,
            (ROOT / 'docs/development/build_ux.md').read_text(encoding='utf-8'),
            (ROOT / 'scripts/README.md').read_text(encoding='utf-8'),
            (ROOT / 'README.md').read_text(encoding='utf-8'),
        ]).lower()
        if phrase.lower() in haystack:
            raise SystemExit(f'forbidden ambiguous command phrase found: {phrase}')

    terminology = (ROOT / 'docs/governance/terminology.md').read_text(encoding='utf-8')
    for term in ['build', 'start', 'ready', 'smoke', 'package', 'release', 'deploy', 'up', 'down']:
        if term not in terminology:
            raise SystemExit(f'terminology glossary missing command term: {term}')

def validate_storage_path_management() -> None:
    storage = read_yaml('configs/storage_paths.yaml')
    if storage.get('source_of_truth') != 'configs/storage_paths.yaml':
        raise SystemExit('storage path registry must declare configs/storage_paths.yaml as source_of_truth')
    paths = storage.get('paths', {})
    model_cache = paths.get('model_cache_dir', {})
    if model_cache.get('env') != 'HF_CACHE_DIR' or model_cache.get('container_path') != '/root/.cache/huggingface':
        raise SystemExit('storage path registry must define HF_CACHE_DIR -> /root/.cache/huggingface')
    compose = (ROOT / 'ops/compose/full-stack.private-network.yaml').read_text(encoding='utf-8')
    if '${HF_CACHE_DIR:-./model_cache/huggingface}:/root/.cache/huggingface' not in compose:
        raise SystemExit('full-stack compose must mount HF_CACHE_DIR into vLLM Hugging Face cache')
    env_example = (ROOT / '.env.compose.example').read_text(encoding='utf-8')
    if 'HF_CACHE_DIR=./model_cache/huggingface' not in env_example:
        raise SystemExit('.env.compose.example must expose HF_CACHE_DIR default')
    for doc_path in ['docs/operations/storage_paths.md', 'docs/operations/configuration_lifecycle.md', 'README.md']:
        text = (ROOT / doc_path).read_text(encoding='utf-8')
        if 'HF_CACHE_DIR' not in text or 'model_cache/huggingface' not in text:
            raise SystemExit(f'{doc_path} must document HF cache storage path')
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    if 'storage-paths:' not in makefile or 'scripts/reports/storage_paths_report.py' not in makefile:
        raise SystemExit('Makefile must expose storage-paths target')
    if 'project-inventory:' not in makefile or 'scripts/reports/project_inventory_report.py' not in makefile:
        raise SystemExit('Makefile must expose project-inventory target')
    project_mgmt_doc = (ROOT / 'docs/operations/project_management_workflow.md').read_text(encoding='utf-8')
    for phrase in ['make project-inventory', 'reports/refactor/project_inventory_current', 'make operator-reports', 'make release-check']:
        if phrase not in project_mgmt_doc:
            raise SystemExit(f'project management UX doc missing phrase: {phrase}')

def validate_monitoring_resource_mapping() -> None:
    monitoring = read_yaml('configs/monitoring.yaml')
    rule_files = monitoring['monitoring_stack']['prometheus'].get('rule_files', [])
    if '/etc/prometheus/rules/model_runtime.rules.yml' not in rule_files:
        raise SystemExit('monitoring config must reference container-mounted model runtime recording rules')
    rules = read_yaml('ops/prometheus/rules/model_runtime.rules.yml')
    records = {rule['record'] for group in rules['groups'] for rule in group['rules'] if 'record' in rule}
    required = {'model_runtime_upstream_error_rate_5m', 'model_runtime_upstream_p95_latency_seconds', 'gpu_memory_headroom_bytes', 'gpu_memory_used_bytes', 'gpu_memory_total_bytes'}
    if not required.issubset(records):
        raise SystemExit(f'model runtime recording rules missing {required - records}')
    prom = (ROOT / 'ops/prometheus/prometheus.yml').read_text(encoding='utf-8')
    if '/etc/prometheus/rules/model_runtime.rules.yml' not in prom:
        raise SystemExit('prometheus.yml must load the container-mounted model_runtime.rules.yml')
    if 'dcgm-exporter:9400' not in prom:
        raise SystemExit('prometheus.yml must scrape dcgm-exporter on its internal default port 9400')
    private_compose = (ROOT / 'ops/compose/full-stack.private-network.yaml').read_text(encoding='utf-8')
    for phrase in ['dcgm-exporter:', '${DCGM_EXPORTER_IMAGE:', 'env_file: ../../.env']:
        if phrase not in private_compose:
            raise SystemExit(f'full-stack private-network compose must include {phrase}')

def validate_operational_hardening_contract() -> None:
    serving = read_yaml('configs/model_serving.yaml')
    timeouts = serving['timeouts']
    models = serving['models']
    risk_adapter_timeout = float(timeouts['risk_adapter_seconds'])
    gateway_timeout = float(timeouts['gateway_request_seconds'])
    if gateway_timeout < risk_adapter_timeout:
        raise SystemExit('gateway_request_seconds must be >= risk_adapter_seconds')
    def detector_budget(model_key: str) -> float:
        model = models[model_key]
        admission = model.get('resource_control', {}).get('admission_control', {})
        queue_timeout = model.get(
            'queue_timeout_seconds',
            admission.get('queue_timeout_seconds', serving['operational_limits']['queue_timeout_seconds']),
        )
        inference_timeout = model.get('timeout_seconds', timeouts['vllm_request_seconds'])
        return float(queue_timeout) + float(inference_timeout)

    detector_keys = [
        detector['service_key']
        for detector in serving['risk_adapter'].get('detectors', {}).values()
        if detector.get('enabled', True) is True and detector.get('type', 'vllm') != 'local'
    ]
    detector_total_budget = sum(detector_budget(key) for key in detector_keys)
    aggregate_cfg = serving['risk_adapter'].get('aggregate', {})
    aggregate_execution = aggregate_cfg.get('execution', serving['risk_adapter'].get('aggregate_execution'))
    if aggregate_execution == 'sequential' and risk_adapter_timeout < detector_total_budget:
        raise SystemExit('risk_adapter_seconds must cover sequential detector queue and inference budgets')
    settings_text = (ROOT / 'src/ai_model_serving/settings.py').read_text(encoding='utf-8')
    for phrase in [
        'API_KEY_REQUIRED=false: Gateway API endpoints are unauthenticated',
        'REQUEST_TIMEOUT_SECONDS must be greater than or equal to RISK_ADAPTER_TIMEOUT_SECONDS',
        'RISK_ADAPTER_TIMEOUT_SECONDS must cover sequential risk detector queue and inference budgets',
        '{env_prefix}_TIMEOUT_SECONDS',
        'admission_control.get("max_concurrency", default_model_concurrency)',
        'admission_control.get("queue_timeout_seconds", default_queue_timeout)',
    ]:
        if phrase not in settings_text:
            raise SystemExit(f'settings.py missing operational hardening: {phrase}')
    validation_text = read_runtime_contract_text()
    for phrase in [
        'stream must be boolean when provided',
        'stream_options.include_usage must be boolean when provided',
        'stream_options may only be provided when stream=true',
        'UNSUPPORTED_CHAT_FIELDS',
        'UNSUPPORTED_MESSAGE_FIELDS',
        'not prompt.strip()',
    ]:
        if phrase not in validation_text:
            raise SystemExit(f'runtime contract validators missing unsupported contract guard: {phrase}')
    chat_schema = read_json('specs/schemas/chat_completion_request.schema.json')
    roles = set(chat_schema['properties']['messages']['items']['properties']['role']['enum'])
    if 'tool' not in roles:
        raise SystemExit('chat completion schema must expose tool role for bounded Gemma4 tool calling')
    serving = read_yaml('configs/model_serving.yaml')
    supported = set(serving['models']['main_llm']['request_parameter_policy']['supported_parameters'])
    schema_parameters = set(chat_schema['properties']) - {'model', 'messages'}
    if supported != schema_parameters:
        raise SystemExit(f'chat request_parameter_policy must match chat schema parameters: config_only={sorted(supported - schema_parameters)}, schema_only={sorted(schema_parameters - supported)}')
    stream_schema = chat_schema['properties'].get('stream', {})
    if stream_schema.get('type') != 'boolean' or 'const' in stream_schema:
        raise SystemExit('chat completion schema must document stream as a supported boolean parameter')
    stream_options_schema = chat_schema['properties'].get('stream_options', {})
    include_usage_schema = stream_options_schema.get('properties', {}).get('include_usage', {}) if isinstance(stream_options_schema, dict) else {}
    if stream_options_schema.get('type') != 'object' or include_usage_schema.get('type') != 'boolean':
        raise SystemExit('chat completion schema must document stream_options.include_usage as a supported boolean parameter')
    schema_text = json.dumps(chat_schema, ensure_ascii=False)
    if '"required": ["stream_options"]' not in schema_text or '"const": true' not in schema_text: raise SystemExit('chat completion schema must require stream=true when stream_options is provided')
    gateway_text = (ROOT / 'src/ai_model_serving/apps/gateway.py').read_text(encoding='utf-8') + (ROOT / 'src/ai_model_serving/api/routers/gateway_inference.py').read_text(encoding='utf-8')
    service_text = (ROOT / 'src/ai_model_serving/services/gateway_service.py').read_text(encoding='utf-8')
    metrics_text = (ROOT / 'src/ai_model_serving/metrics.py').read_text(encoding='utf-8')
    streaming_ops = (ROOT / 'docs/operations/streaming_runtime_operations.md').read_text(encoding='utf-8')
    for phrase in [
        'StreamingResponse',
        'X-Accel-Buffering',
        'text/event-stream',
        'stream_chat_completion',
        'event: error',
        'streaming_usage_events_total',
        'proxy_buffering off',
        'stream_options.include_usage',
        'chat.completion.chunk',
    ]:
        if phrase not in (gateway_text + service_text + metrics_text + streaming_ops):
            raise SystemExit(f'streaming contract must keep implementation and operations policy aligned: {phrase}')
    for field in ['tools', 'tool_choice', 'parallel_tool_calls']:
        if field not in chat_schema['properties']:
            raise SystemExit(f'chat completion schema must document tool-calling field: {field}')
    if chat_schema['properties']['parallel_tool_calls'].get('const') is not False:
        raise SystemExit('parallel_tool_calls must remain false until parallel tool calls are explicitly enabled')
    message_props = chat_schema['properties']['messages']['items']['properties']
    for field in ['tool_calls', 'tool_call_id']:
        if field not in message_props:
            raise SystemExit(f'chat completion schema must document supported message field: {field}')

def validate_internal_service_auth_contract() -> None:
    serving = read_yaml('configs/model_serving.yaml')
    security = serving.get('security', {})
    if security.get('internal_service_token') != 'change-me-internal':
        raise SystemExit('model_serving.yaml should carry the local placeholder internal service token')
    if security.get('internal_service_auth') != 'gateway_to_risk_adapter_only':
        raise SystemExit('security.internal_service_auth must document gateway_to_risk_adapter_only')
    gateway = (ROOT / 'src/ai_model_serving/apps/gateway.py').read_text(encoding='utf-8')
    gateway_service = (ROOT / 'src/ai_model_serving/services/gateway_service.py').read_text(encoding='utf-8')
    risk_adapter = (ROOT / 'src/ai_model_serving/apps/risk_adapter.py').read_text(encoding='utf-8')
    if 'settings.security.internal_service_token' not in (gateway + gateway_service):
        raise SystemExit('Gateway must call Risk Adapter with the internal service token')
    if 'frozenset({settings.security.internal_service_token})' not in risk_adapter:
        raise SystemExit('Risk Adapter must accept only the internal service token for protected endpoints')
    if 'INTERNAL_SERVICE_TOKEN=change-me-internal' not in (ROOT / '.env.example').read_text(encoding='utf-8'):
        raise SystemExit('.env.example must document INTERNAL_SERVICE_TOKEN')

def validate_multimodal_chat_and_token_caps() -> None:
    chat_schema = read_json('specs/schemas/chat_completion_request.schema.json')
    content_schema = chat_schema['properties']['messages']['items']['properties']['content']
    if 'oneOf' not in content_schema:
        raise SystemExit('chat completion schema must support both string content and content parts')
    image_schema_text = json.dumps(content_schema)
    if 'image_url' not in image_schema_text or 'data:image' not in image_schema_text:
        raise SystemExit('chat completion schema must expose bounded image_url content parts')
    expected_max_tokens = int(read_yaml('configs/gpu_budgets.yaml')['limits']['main_llm_max_output_tokens'])
    if chat_schema['properties']['max_tokens'].get('maximum') != expected_max_tokens:
        raise SystemExit(f'chat completion schema max_tokens.maximum must match gpu_budgets.yaml main_llm_max_output_tokens {expected_max_tokens}')
    validation = read_runtime_contract_text()
    for phrase in [
        'image_url.url scheme must be one of',
        'max_tokens must be less than or equal to',
        'at most {max_image_inputs} image',
        'decoded image must be',
        'image dimensions',
        'MIME type must be one of',
        'valid base64',
    ]:
        if phrase not in validation:
            raise SystemExit(f'runtime contract validators must enforce multimodal bounds and token caps: {phrase}')
    gateway = (ROOT / 'src/ai_model_serving/apps/gateway.py').read_text(encoding='utf-8')
    gateway_service = (ROOT / 'src/ai_model_serving/services/gateway_service.py').read_text(encoding='utf-8')
    for phrase in ['validate_chat_response', 'validate_embedding_response']:
        if phrase not in (gateway + gateway_service):
            raise SystemExit(f'gateway.py must validate upstream response schemas: {phrase}')
