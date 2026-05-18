from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit('PyYAML is required: pip install pyyaml') from exc


def find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / 'VERSION').exists() and (parent / 'configs').exists():
            return parent
    raise RuntimeError('could not locate project root from governance_validation package')


ROOT = find_project_root()

REQUIRED_FILES = [
    'README.md',
    'docs/START_HERE.md',
    'VERSION',
    'version_manifest.json',
    '.env.example',
    '.env.local.example',
    '.env.compose.example',
    'Makefile',
    '.python-version',
    'pyproject.toml',
    'requirements.lock',
    'requirements.runtime.lock',
    'configs/model_catalog.yaml',
    'configs/model_serving.yaml',
    'configs/ports.yaml',
    'configs/risk_taxonomy.yaml',
    'configs/runtime_compatibility.yaml',
    'configs/monitoring.yaml',
    'configs/storage_paths.yaml',
    'docs/models/model_cards.md',
    'docs/models/gemma4_31b_awq_review.md',
    'docs/development/build_ux.md',
    'docs/development/python_compatibility.md',
    'docs/operations/monitoring_ux.md',
    'docs/operations/status_board_ux.md',
    'docs/operations/operator_workflows.md',
    'docs/operations/configuration_lifecycle.md',
    'docs/operations/storage_paths.md',
    'docs/operations/project_management_ux.md',
    'docs/operations/risk_vllm_patch_lifecycle.md',
    'scripts/models/model_inventory.py',
    'ops/docker/Dockerfile.risk-vllm-kanana',
    'ops/patches/README.md',
    'ops/patches/transformers_llama_head_dim_guard.py',
    'scripts/reports/operator_guide.py',
    'ops/compose/full-stack.example.yaml',
    'ops/prometheus/rules/model_runtime.rules.yml',
    'docs/operations/ux_ui_review.md',
    'docs/operations/full_stack_runtime.md',
    'docs/operations/full_stack_troubleshooting.md',
    'docs/operations/model_runtime_control.md',
    'docs/operations/gitlab_cicd_deployment.md',
    'docs/operations/runtime_validation_workplan.md',
    'docs/governance/document_management.md',
    'harness/runtime_validation_matrix.yaml',
    'harness/runtime_validation_plan.md',
    'ops/grafana/dashboards/risk_signal_operations.json',
    'ops/grafana/dashboards/serving_home.json',
    'ops/grafana/dashboards/gpu_capacity_and_oom_risk.json',
    'ops/grafana/dashboards/executive_runtime_overview.json',
    'ops/grafana/dashboards/api_experience.json',
    'ops/grafana/dashboards/model_runtime_deep_dive.json',
    'ops/grafana/dashboards/observability_data_quality.json',
    'ops/prometheus/prometheus.yml',
    'docs/resources/README.md',
    'docs/resources/gpu_resource_requirements_48gb.md',
    'docs/development/README.md',
    'examples/requests/README.md',
    'docs/governance/policies/retired_source_cleanup_policy.md',
    'configs/retired_source_cleanup_policy.yaml',
    'docs/governance/policies/command_terminology_policy.md',
    'configs/command_terminology_policy.yaml',
    'scripts/validation/run_tests.py',
    'scripts/compose/preflight_compose.sh',
    'scripts/compose/compose_diagnostics.sh',
    'scripts/compose/validate_vllm_compose.py',
    'scripts/models/render_vllm_commands.py',
    'scripts/reports/runtime_targets_report.py',
    'scripts/reports/storage_paths_report.py',
    'scripts/reports/project_inventory_report.py',
    'scripts/reports/monitoring_projection_report.py',
    'scripts/reports/operator_status_bundle.py',
    'scripts/reports/live_evidence_bundle.py',
    'scripts/validation/release_check.py',
    'scripts/ops/start_services.sh',
    'scripts/ops/up_services.sh',
    'scripts/ops/stop_services.sh',
    'scripts/ops/down_services.sh',
    'scripts/ops/status_services.sh',
    'scripts/ops/ready_check.sh',
    'scripts/ops/check_ready.sh',
    'scripts/lib/load_env.sh',
    'docs/release/versioning_policy.md',
    'docs/release/release_checklist.md',
    'docs/specs/api.md',
    'specs/openapi.gateway.yaml',
    'specs/openapi.risk-adapter.yaml',
    'specs/schemas/risk_assessment_request.schema.json',
    'specs/schemas/risk_assessment_response.schema.json',
    'specs/schemas/chat_completion_request.schema.json',
    'specs/schemas/embedding_request.schema.json',
    'specs/schemas/common_error.schema.json',
    'specs/schemas/chat_completion_response.schema.json',
    'specs/schemas/embedding_response.schema.json',
    'specs/schemas/readiness_response.schema.json',
    'src/ai_model_serving/settings.py',
    'src/ai_model_serving/openapi_contracts.py',
    'src/ai_model_serving/status.py',
    'src/ai_model_serving/domain/model_registry.py',
    'src/ai_model_serving/operator_reports.py',
    'src/ai_model_serving/monitoring_projection.py',
    'src/ai_model_serving/operator_status.py',
    'src/ai_model_serving/storage_paths.py',
    'src/ai_model_serving/project_inventory.py',
    'src/ai_model_serving/live_evidence.py',
    'src/ai_model_serving/governance_validation/__init__.py',
    'src/ai_model_serving/governance_validation/cli.py',
    'src/ai_model_serving/governance_validation/common.py',
    'src/ai_model_serving/governance_validation/filesystem.py',
    'src/ai_model_serving/governance_validation/versioning.py',
    'src/ai_model_serving/governance_validation/schemas.py',
    'src/ai_model_serving/governance_validation/model_config.py',
    'src/ai_model_serving/governance_validation/docs_ops.py',
    'src/ai_model_serving/governance_validation/monitoring_dashboards.py',
    'src/ai_model_serving/governance_validation/release_runtime.py',
    'src/ai_model_serving/errors.py',
    'src/ai_model_serving/security.py',
    'src/ai_model_serving/upstream.py',
    'src/ai_model_serving/risk.py',
    'src/ai_model_serving/middleware.py',
    'src/ai_model_serving/apps/gateway.py',
    'src/ai_model_serving/apps/risk_adapter.py',
    'tests/unit/test_gateway_app.py',
    'tests/unit/test_openapi_contracts.py',
    'tests/unit/test_risk_adapter_app.py',
    'tests/unit/test_governance_validation_package.py',
    'tests/unit/test_model_registry.py',
    'tests/unit/test_runtime_validation_package.py',
]

FORBIDDEN_RESPONSE_FIELDS = {
    'allow', 'review', 'block', 'decision', 'action', 'safe_to_send',
    'final_decision', 'final_decision_owner', 'policy_overrides'
}
EXPECTED_PORTS = {
    'gateway': 9400,
    'main_llm_vllm': 9401,
    'embedding_vllm': 9402,
    'risk_prompt_vllm': 9403,
    'colbert_ko_vllm': 9404,
    'risk_adapter': 9405,
}
EXCLUDED_SCAN_PARTS = {
    '.git', '.venv', 'venv', 'env', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', '.eggs', 'dist', 'build', 'node_modules',
    '.runtime', 'model_cache', 'models', 'logs', 'run', 'outputs', '.cache',
    '.other',
}


def is_excluded_project_path(path: Path) -> bool:
    try:
        rel_parts = set(path.relative_to(ROOT).parts)
    except ValueError:
        return True
    return bool(rel_parts & EXCLUDED_SCAN_PARTS)


def iter_project_files(pattern: str = '*'):
    for candidate in ROOT.rglob(pattern):
        if candidate.is_file() and not is_excluded_project_path(candidate):
            yield candidate


def read_json(path: str) -> Any:
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def read_yaml(path: str) -> Any:
    return yaml.safe_load((ROOT / path).read_text(encoding='utf-8'))


def read_runtime_contract_text() -> str:
    paths = [
        'src/ai_model_serving/validation.py',
        'src/ai_model_serving/contracts/chat.py',
        'src/ai_model_serving/contracts/embedding.py',
        'src/ai_model_serving/contracts/media.py',
        'src/ai_model_serving/contracts/risk.py',
    ]
    return "\n".join((ROOT / path).read_text(encoding='utf-8') for path in paths)
