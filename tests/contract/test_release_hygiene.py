from __future__ import annotations

import json
import os
import subprocess
import zipfile
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_detailed_retired_source_inventory_removed() -> None:
    forbidden = [
        'reports/source_file_inventory.csv',
        'docs/reviews/source_file_inventory_summary.md',
        'reports/full_project_model_feature_review_2026-05-06.md',
        'reports/project_ux_and_hardening_review_2026-05-06.md',
        'reports/operational_ux_hardening_review_0.1.6_2026-05-06.md',
        'reports/env_image_automation_review_0.1.7_2026-05-06.md',
        'reports/maintenance_version_rebaseline_0.1.0-rc.1_2026-05-06.md',
    ]
    for rel in forbidden:
        assert not (ROOT / rel).exists(), rel


def test_package_script_excludes_env_glob_and_large_artifacts() -> None:
    text = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    for pattern in ['.env.*', 'logs', 'dist', 'model_cache', 'models', '.egg-info', '.venv', 'venv']:
        assert pattern in text


def test_package_script_excludes_virtualenv_directories() -> None:
    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    for dirname in ["'.venv'", "'venv'", "'env'", "'.tox'"]:
        assert dirname in package_script


def test_redundant_origin_adr_removed_but_decision_retained() -> None:
    assert not (ROOT / 'adr/0001-origin-transition.md').exists()
    decision_register = (ROOT / 'docs/02_decision_register.md').read_text(encoding='utf-8')
    source_review = (ROOT / 'docs/01_project_background.md').read_text(encoding='utf-8')
    assert 'D-001' in decision_register
    assert '과거 원천 프로젝트 코드는 포함하지 않는다' in decision_register
    assert 'ADR-0001은 별도 파일로 유지하지 않는다' in source_review



def test_release_zip_hygiene_when_present() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    package = ROOT / "dist" / f"ai_model_serving_platform_{version}.zip"
    if not package.exists():
        return
    with zipfile.ZipFile(package) as zf:
        names = zf.namelist()
        infos = zf.infolist()
    forbidden_fragments = [".pytest_cache", "__pycache__", ".pyc", "/dist/", ".egg-info", "/.git/"]
    assert not any(any(fragment in name for fragment in forbidden_fragments) for name in names)
    assert names
    assert all(name == "ai_model_serving_platform/" or name.startswith("ai_model_serving_platform/") for name in names)
    # Timestamps must be normalized so two builds from identical source produce identical ZIPs.
    _EPOCH = (1980, 1, 1, 0, 0, 0)
    non_normalized = [i.filename for i in infos if i.date_time != _EPOCH]
    assert not non_normalized, f"non-normalized timestamps in zip: {non_normalized[:5]}"


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


def test_runtime_pid_artifacts_are_not_packaged() -> None:
    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    clean_script = (ROOT / 'scripts/ops/clean_all.sh').read_text(encoding='utf-8')
    assert '"$BASE/run/*"' in package_script
    assert '"$ROOT/run"' in clean_script
    assert '"$ROOT/ops/compose/model_cache"' in clean_script


def test_safe_env_examples_are_packaged_explicitly() -> None:
    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    for name in ['.env.example', '.env.local.example', '.env.compose.example']:
        assert f'"$BASE/{name}"' in package_script

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
    assert 'jsonschema==' not in runtime_lockfile
    assert 'COPY pyproject.toml requirements.lock requirements.runtime.lock README.md VERSION ./' in dockerfile
    assert '--requirement requirements.runtime.lock' in dockerfile
    assert '--no-deps .' in dockerfile


def test_safe_env_examples_and_runtime_reports_packaging_policy():
    package_script = Path("scripts/build/package_release.sh").read_text()
    gitignore = Path(".gitignore").read_text()
    dockerignore = Path(".dockerignore").read_text()

    for safe_example in ['!.env.example', '!.env.local.example', '!.env.compose.example']:
        assert safe_example in gitignore
        assert safe_example in dockerignore

    assert 'reports/runtime/runtime_validation_*.json' in package_script
    assert 'reports/runtime/runtime_validation_*.md' in package_script



def test_clean_removes_python_build_metadata() -> None:
    clean_script = (ROOT / 'scripts/ops/clean_all.sh').read_text(encoding='utf-8')
    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    assert "*.egg-info" in clean_script
    assert ".egg-info" in package_script


def test_release_package_uses_stable_root_directory() -> None:
    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    assert 'PACKAGE_ROOT="${PACKAGE_ROOT:-ai_model_serving_platform}"' in package_script
    # ZIP is created via Python zipfile; PACKAGE_ROOT is passed as the staging source directory.
    assert '"$STAGE/$PACKAGE_ROOT" "$OUT"' in package_script
    assert 'zipfile.ZipFile' in package_script


def test_preflight_compose_checks_only_host_published_ports() -> None:
    text = (ROOT / 'scripts/compose/preflight_compose.sh').read_text(encoding='utf-8')
    assert 'vLLM runtime ports 9401-9403 are internal' in text
    loop_line = next(line for line in text.splitlines() if line.strip().startswith('for port in'))
    for internal_port in ['9401', '9402', '9403']:
        assert internal_port not in loop_line
    assert 'host_published_ports=(' in text
    for env_name in ['PROMETHEUS_PORT', 'GRAFANA_PORT', 'DCGM_EXPORTER_PORT', 'CADVISOR_PORT']:
        assert env_name in text
    assert '9410 9411 9412 9413' not in text


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


def test_release_zip_hygiene_forbids_runtime_secret_directory_when_present() -> None:
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    package = ROOT / 'dist' / f'ai_model_serving_platform_{version}.zip'
    if not package.exists():
        return
    with zipfile.ZipFile(package) as zf:
        names = zf.namelist()
    assert not any('/.runtime/' in name or name.startswith('ai_model_serving_platform/.runtime/') for name in names)


def test_make_package_runs_static_validation_before_zipping() -> None:
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    assert 'refresh-generated-reports:' in makefile
    assert 'package: refresh-generated-reports' in makefile
    package_block = makefile.split('package: refresh-generated-reports', 1)[1].split('\n\n', 1)[0]
    assert 'scripts/build/check_python.py --context package' in package_block
    assert 'scripts/validation/validate_contracts.py' in package_block
    assert 'PACKAGE_SKIP_VALIDATION=1 bash scripts/build/package_release.sh' in package_block
    assert package_block.index('scripts/validation/validate_contracts.py') < package_block.index('PACKAGE_SKIP_VALIDATION=1 bash scripts/build/package_release.sh')

    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    assert 'PACKAGE_SKIP_VALIDATION' in package_script
    assert 'scripts/build/check_python.py" --context package' in package_script
    assert 'scripts/validation/validate_contracts.py"' in package_script
    assert package_script.index('scripts/validation/validate_contracts.py"') < package_script.index('zipfile.ZipFile')


def test_package_script_preserves_documentation_build_directory() -> None:
    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    assert 'exclude_top_level_dirs' in package_script
    assert 'exclude_tree_dirs' in package_script
    assert 'top in exclude_top_level_dirs' in package_script
    assert 'docs/build/build_ux.md' not in package_script


def test_vllm_compose_validation_and_diagnostics_are_packaged() -> None:
    assert (ROOT / 'scripts/compose/validate_vllm_compose.py').exists()
    assert (ROOT / 'scripts/compose/compose_diagnostics.sh').exists()
    validate_script = (ROOT / 'scripts/validation/validate_contracts.py').read_text(encoding='utf-8')
    preflight_script = (ROOT / 'scripts/compose/preflight_compose.sh').read_text(encoding='utf-8')
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


def test_compose_env_example_does_not_embed_real_huggingface_token() -> None:
    env = (ROOT / '.env.compose.example').read_text(encoding='utf-8')
    assert 'HF_TOKEN=' in env
    assert 'HUGGING_FACE_HUB_TOKEN=' in env
    assert not re.search(r'\bhf_[A-Za-z0-9_-]{20,}\b', env)
    assert 'READY_FULL_TIMEOUT_SECONDS=1800' in env


def test_package_script_prunes_excluded_directories_during_copy() -> None:
    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    assert 'os.walk(src)' in package_script
    assert 'dirnames[:] = kept_dirs' in package_script
    assert 'skip_dir' in package_script


def test_package_script_excludes_timestamped_runtime_validation_evidence() -> None:
    package_script = (ROOT / 'scripts/build/package_release.sh').read_text(encoding='utf-8')
    assert "fnmatch.fnmatch(name, 'runtime_validation_*.json')" in package_script
    assert "fnmatch.fnmatch(name, 'runtime_validation_*.md')" in package_script
    assert 'reports/runtime/runtime_validation_*.md' in package_script
    assert 'reports/runtime/runtime_validation_*.json' in package_script
    assert 'staged live_evidence_bundle is regenerated without timestamped runtime evidence' in package_script


def test_refactor_cleanup_policy_blocks_stale_current_snapshots() -> None:
    import yaml

    policy = yaml.safe_load((ROOT / 'configs/retired_source_cleanup_policy.yaml').read_text(encoding='utf-8'))
    patterns = policy['prohibited_refactor_report_patterns']
    assert 'reports/refactor/current_refactor_state_*.md' in patterns
    assert 'reports/refactor/project_inventory_phase*.csv' in patterns
    assert 'reports/refactor/project_inventory_phase*.json' in patterns
    assert 'reports/refactor/project_inventory_phase*.md' in patterns


def test_release_package_rewrites_live_evidence_without_excluded_runtime_validation() -> None:
    runtime_dir = ROOT / 'reports/runtime'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    fake_json = runtime_dir / 'runtime_validation_20990101T000000Z.json'
    fake_md = runtime_dir / 'runtime_validation_20990101T000000Z.md'
    fake_json.write_text(json.dumps({
        'mode': 'live',
        'summary': {'passed': 1, 'failed': 0},
        'results': [{'category': 'gateway-runtime', 'name': 'fake', 'status': 'pass'}],
    }), encoding='utf-8')
    fake_md.write_text('# fake runtime validation\n', encoding='utf-8')
    try:
        env = os.environ.copy()
        env['PACKAGE_SKIP_VALIDATION'] = '1'
        subprocess.run(['bash', 'scripts/build/package_release.sh'], cwd=ROOT, env=env, check=True, capture_output=True, text=True, timeout=60)
        version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
        package = ROOT / 'dist' / f'ai_model_serving_platform_{version}.zip'
        with zipfile.ZipFile(package) as zf:
            names = zf.namelist()
            assert not any('runtime_validation_20990101T000000Z' in name for name in names)
            evidence = json.loads(zf.read('ai_model_serving_platform/reports/runtime/live_evidence_bundle.json'))
        assert evidence['runtime_report']['path'] is None
        assert evidence['runtime_report']['mode'] == 'missing'
        assert evidence['evidence_status'] == 'missing_runtime_evidence'
    finally:
        fake_json.unlink(missing_ok=True)
        fake_md.unlink(missing_ok=True)


def test_release_package_keeps_documentation_models_directory() -> None:
    env = os.environ.copy()
    env['PACKAGE_SKIP_VALIDATION'] = '1'
    subprocess.run(['bash', 'scripts/build/package_release.sh'], cwd=ROOT, env=env, check=True, capture_output=True, text=True, timeout=60)
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    package = ROOT / 'dist' / f'ai_model_serving_platform_{version}.zip'
    with zipfile.ZipFile(package) as zf:
        names = set(zf.namelist())
    assert 'ai_model_serving_platform/docs/models/model_cards.md' in names
    assert 'ai_model_serving_platform/docs/models/gemma4_31b_awq_review.md' in names
    assert 'ai_model_serving_platform/docs/models/model_runtime_source_review.md' in names
    assert not any(name.startswith('ai_model_serving_platform/models/') for name in names)


def test_status_services_reports_readiness_phase_and_not_ready_dependencies() -> None:
    text = (ROOT / 'scripts/ops/status_services.sh').read_text(encoding='utf-8')
    assert 'phase = doc.get("phase", "unknown")' in text
    assert 'not_ready_dependencies' in text
    assert 'status={status} phase={phase}' in text



def test_makefile_exposes_candidate_env_and_model_proposal_ux() -> None:
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    assert 'AUTH_ENV ?= $(ENV)' in makefile
    assert 'scripts/auth/auth_status.py $(AUTH_ENV_ARG)' in makefile
    assert 'scripts/auth/auth_doctor.py $(AUTH_ENV_ARG) --warn-only' in makefile
    assert 'model-propose-add' in makefile
    assert 'model-propose-remove' in makefile
    assert 'scripts/models/modelctl.py propose-add' in makefile
    assert 'scripts/models/modelctl.py propose-remove' in makefile


def test_active_maintainability_doc_does_not_contain_stale_phase30_completed_work() -> None:
    text = (ROOT / 'docs/operations/project_state_maintainability_review.md').read_text(encoding='utf-8')
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
