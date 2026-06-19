from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_redundant_origin_adr_removed_but_decision_retained() -> None:
    assert not (ROOT / 'docs/adr/0001-origin-transition.md').exists()
    decision_register = (ROOT / 'docs/02_decision_register.md').read_text(encoding='utf-8')
    source_review = (ROOT / 'docs/01_project_background.md').read_text(encoding='utf-8')
    assert 'D-001' in decision_register
    assert '과거 원천 프로젝트 코드는 포함하지 않는다' in decision_register
    assert 'ADR-0001은 별도 파일로 유지하지 않는다' in source_review

def test_retired_source_cleanup_policy_exists_and_is_enforced() -> None:
    policy_doc = ROOT / 'docs/governance/policies/retired_source_cleanup_policy.md'
    policy_yaml = ROOT / 'configs/retired_source_cleanup_policy.yaml'
    assert policy_doc.exists()
    assert policy_yaml.exists()
    text = policy_doc.read_text(encoding='utf-8')
    assert 'Retired Source Cleanup Policy' in text
    assert 'D-001' in text
    assert 'ADR-0001' in text
    assert 'runtime mock' in text.lower() or 'Runtime mock' in text

    import yaml
    policy = yaml.safe_load(policy_yaml.read_text(encoding='utf-8'))
    assert policy['policy_name'] == 'retired_source_cleanup_policy'
    assert policy['mock_policy']['runtime_mock_allowed'] is False
    assert 'reports/source_file_inventory.csv' in policy['prohibited_release_paths']
    assert 'docs/reviews/source_file_inventory_summary.md' in policy['prohibited_release_paths']
    assert 'reports/full_project_model_feature_review_2026-05-06.md' in policy['prohibited_release_paths']
    assert 'reports/project_ux_and_hardening_review_2026-05-06.md' in policy['prohibited_release_paths']
    assert 'reports/operational_ux_hardening_review_0.1.6_2026-05-06.md' in policy['prohibited_release_paths']
    assert 'reports/env_image_automation_review_0.1.7_2026-05-06.md' in policy['prohibited_release_paths']
    assert 'reports/maintenance_version_rebaseline_0.1.0-rc.1_2026-05-06.md' in policy['prohibited_release_paths']


def test_lockfiles_and_dockerfile_are_aligned_with_runtime_install_contract():
    pyproject = Path("pyproject.toml").read_text()
    full_lockfile = Path("requirements.lock").read_text()
    runtime_lockfile = Path("requirements.runtime.lock").read_text()
    dockerfile = Path("Dockerfile").read_text()

    assert 'pytest==8.4.2' in pyproject
    assert 'anyio==4.9.0' in pyproject
    assert 'pytest==8.' in full_lockfile
    assert 'jsonschema==' in full_lockfile
    assert 'pytest==9.' not in full_lockfile
    assert 'pytest==' not in runtime_lockfile
    assert 'jsonschema==4.26.0' in runtime_lockfile
    assert 'COPY pyproject.toml requirements.lock requirements.runtime.lock README.md VERSION ./' in dockerfile
    assert '--requirement requirements.runtime.lock' in dockerfile
    assert '--no-deps .' in dockerfile


def test_safe_env_examples_and_runtime_reports_packaging_policy():
    gitignore = Path(".gitignore").read_text()
    dockerignore = Path(".dockerignore").read_text()

    for safe_example in ['!.env.example', '!.env.local.example', '!.env.compose.example']:
        assert safe_example in gitignore
        assert safe_example in dockerignore

def test_ready_local_is_strict_health_gate() -> None:
    text = (ROOT / 'scripts/ops/ready_local.sh').read_text(encoding='utf-8')
    assert 'fail=0' in text
    assert 'exit 1' in text
    assert '/health unavailable' in text
    assert 'make ready-full' in text


def test_runtime_secret_directory_policy_matches_local_workflow() -> None:
    """Local compose may create .runtime, but packaged artifacts must never include it."""
    gitignore = (ROOT / '.gitignore').read_text(encoding='utf-8')
    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    clean_script = (ROOT / 'scripts/ops/clean_all.sh').read_text(encoding='utf-8')

    assert '.runtime/' in gitignore
    assert "'.runtime'" in package_script
    assert 'PURGE_RUNTIME_SECRETS' in clean_script
    assert 'Set PURGE_RUNTIME_SECRETS=1' in clean_script

def test_make_package_runs_static_validation_before_zipping() -> None:
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    assert 'refresh-generated-reports:' in makefile
    assert 'package: refresh-generated-reports' in makefile
    package_block = makefile.split('package: refresh-generated-reports', 1)[1].split('\n\n', 1)[0]
    assert 'scripts/build/check_python.py --context package' in package_block
    assert 'scripts/validation/validate_contracts.py' in package_block
    assert 'PACKAGE_SKIP_VALIDATION=1 bash scripts/build/package_release.sh' in package_block
    assert package_block.index('scripts/validation/validate_contracts.py') < package_block.index('PACKAGE_SKIP_VALIDATION=1 bash scripts/build/package_release.sh')

def test_vllm_compose_validation_and_diagnostics_are_packaged() -> None:
    assert (ROOT / 'scripts/compose/validate_vllm_compose.py').exists()
    assert (ROOT / 'scripts/compose/compose_diagnostics.sh').exists()


def test_gitlab_ci_runs_model_and_compose_contract_gates() -> None:
    ci = (ROOT / '.gitlab-ci.yml').read_text(encoding='utf-8')
    assert 'python scripts/compose/validate_vllm_compose.py' in ci
    assert 'hf-main-model-profiles-canary:' in ci
    assert 'scripts/models/check_main_model_profiles.py' in ci
    assert 'HF_CANARY_VENV="$(mktemp -d)"' in ci
    assert "trap 'rm -rf \"$HF_CANARY_VENV\"' EXIT" in ci
    assert '"huggingface_hub==1.13.0"' in ci
    assert "gemma4-26b-a4b-fp8" not in ci
    assert "gemma4-12b-unified-fp8" not in ci
    canary = ci.split("hf-main-model-profiles-canary:", 1)[1].split(
        "\nunit-test:", 1
    )[0]
    assert "only:" not in canary
    validate_script = (ROOT / 'scripts/validation/validate_contracts.py').read_text(encoding='utf-8')
    preflight_script = (ROOT / 'scripts/compose/preflight_compose.py').read_text(encoding='utf-8')
    ready_full_script = (ROOT / 'scripts/ops/ready_full.sh').read_text(encoding='utf-8')
    assert 'validate_vllm_compose.py' in validate_script
    assert 'validate_vllm_compose.py' in preflight_script
    assert 'check_risk_vllm_image_config.sh' in preflight_script
    assert 'SKIP_RISK_VLLM_IMAGE_CONFIG_CHECK' in preflight_script
    assert 'compose_diagnostics.sh' in ready_full_script


def test_ready_full_is_compose_aware_and_retries_transient_startup() -> None:
    text = (ROOT / 'scripts/ops/ready_full.sh').read_text(encoding='utf-8')
    assert 'READY_FULL_TIMEOUT_SECONDS' in text
    assert 'READY_FULL_TIMEOUT_SECONDS:-1800' in text
    assert 'wait_for_probe "gateway /health"' in text
    assert 'risk-adapter /health' in text
    assert 'compose services do not have' in text
    assert 'run_diagnostics' in text
    assert 'status_pid gateway' not in text
    # A control-plane redeploy closes the main-model gate; ready-full must wait for
    # local-main chat to actually serve (gate reopened) before the strict smoke gate,
    # and inference warmup must be best-effort so it can never abort the deploy.
    assert 'wait_for_main_model_ready' in text, (
        "ready-full must wait for the main-model gate to reopen before smoke"
    )
    assert 'READY_FULL_MAIN_MODEL_TIMEOUT_SECONDS' in text
    assert 'warm_inference_paths_best_effort' in text, (
        "inference warmup must be best-effort, not a deploy-aborting gate"
    )


def test_compose_env_example_does_not_embed_real_huggingface_token() -> None:
    env = (ROOT / '.env.compose.example').read_text(encoding='utf-8')
    assert 'HF_TOKEN=' in env
    assert 'HUGGING_FACE_HUB_TOKEN=' in env
    assert not re.search(r'\bhf_[A-Za-z0-9_-]{20,}\b', env)
    assert 'READY_FULL_TIMEOUT_SECONDS=1800' in env


def test_refactor_cleanup_policy_blocks_stale_current_snapshots() -> None:
    import yaml

    policy = yaml.safe_load((ROOT / 'configs/retired_source_cleanup_policy.yaml').read_text(encoding='utf-8'))
    patterns = policy['prohibited_refactor_report_patterns']
    assert 'reports/refactor/current_refactor_state_*.md' in patterns
    assert 'reports/refactor/project_inventory_phase*.csv' in patterns
    assert 'reports/refactor/project_inventory_phase*.json' in patterns
    assert 'reports/refactor/project_inventory_phase*.md' in patterns

def test_status_services_reports_readiness_phase_and_not_ready_dependencies() -> None:
    text = (ROOT / 'scripts/ops/status_services.sh').read_text(encoding='utf-8')
    assert 'phase = doc.get("phase", "unknown")' in text
    assert 'not_ready_dependencies' in text
    assert 'status={status} phase={phase}' in text



def test_makefile_exposes_candidate_env_and_model_proposal_ux() -> None:
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    assert 'AUTH_ENV ?= $(if $(ENV_FILE),$(ENV_FILE),$(ENV))' in makefile
    assert 'scripts/auth/auth_status.py $(AUTH_ENV_ARG)' in makefile
    assert 'scripts/auth/auth_doctor.py $(AUTH_ENV_ARG) --warn-only' in makefile
    assert 'model-propose-add' in makefile
    assert 'model-propose-remove' in makefile
    assert 'scripts/models/modelctl.py propose-add' in makefile
    assert 'scripts/models/modelctl.py propose-remove' in makefile


def test_active_maintainability_doc_does_not_contain_stale_phase30_completed_work() -> None:
    text = (ROOT / 'docs/operations/project_maintainability_status.md').read_text(encoding='utf-8')
    assert 'Phase 24 기준' not in text
    assert 'OpenAPI full snapshot diff를 release gate에 추가한다' not in text
    assert 'Risk patch removal check를 명시적 명령으로 추가한다' not in text
    assert 'modelctl propose-add` / `modelctl propose-remove`는 바로 apply하지 말고 plan 파일 생성부터 시작한다' not in text


def test_dated_root_reports_are_retired_from_active_tree() -> None:
    assert not (ROOT / 'reports/maintenance_version_rebaseline_0.1.0-rc.1_2026-05-06.md').exists()


def test_refresh_generated_reports_uses_static_live_evidence_placeholder() -> None:
    text = (ROOT / 'scripts/reports/refresh_generated_reports.py').read_text(encoding='utf-8')
    assert 'live_evidence_bundle.py' in text
    assert '--static-placeholder' in text
    live = (ROOT / 'scripts/reports/live_evidence_bundle.py').read_text(encoding='utf-8')
    assert '--static-placeholder' in live


def test_current_project_inventory_does_not_list_removed_legacy_reports() -> None:
    removed = 'reports/maintenance_version_rebaseline_0.1.0-rc.1_2026-05-06.md'
    for rel in [
        'reports/refactor/project_inventory_current.csv',
        'reports/refactor/project_inventory_current.json',
        'reports/refactor/project_inventory_current.md',
    ]:
        text = (ROOT / rel).read_text(encoding='utf-8')
        assert removed not in text


def test_image_tags_are_package_version_aligned() -> None:
    """platform image와 risk_vllm image tag가 모두 package version과 일치해야 한다."""
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    manifest = json.loads((ROOT / 'version_manifest.json').read_text(encoding='utf-8'))
    image_tags = manifest.get('image_tags', {})
    assert image_tags.get('platform') == f'ai-model-serving-platform:{version}', \
        f"version_manifest image_tags.platform mismatch: {image_tags.get('platform')} != ai-model-serving-platform:{version}"
    assert image_tags.get('risk_vllm') == f'ai-model-serving-risk-vllm-kanana:{version}', \
        f"version_manifest image_tags.risk_vllm mismatch: {image_tags.get('risk_vllm')} != ai-model-serving-risk-vllm-kanana:{version}"

    import yaml as _yaml
    images = _yaml.safe_load((ROOT / 'configs/recommended_images.yaml').read_text(encoding='utf-8'))['images']
    assert images['platform']['default'] == f'ai-model-serving-platform:{version}', \
        f"recommended_images platform mismatch: {images['platform']['default']}"
    assert images['risk_vllm']['default'] == f'ai-model-serving-risk-vllm-kanana:{version}', \
        f"recommended_images risk_vllm mismatch: {images['risk_vllm']['default']}"

    compose_env = (ROOT / '.env.compose.example').read_text(encoding='utf-8')
    assert f'PLATFORM_IMAGE=ai-model-serving-platform:{version}' in compose_env
    assert f'RISK_VLLM_IMAGE=ai-model-serving-risk-vllm-kanana:{version}' in compose_env
