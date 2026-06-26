from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_build_ux_separates_build_from_runtime_startup() -> None:
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    assert 'make build         # build artifacts/images only; does not start or keep services alive' in makefile
    for target in ['start:', 'up:', 'ready:', 'check-ready:', 'status:', 'stop:', 'down:', 'logs:', 'build-pipeline:', 'first-run:', 'rebuild-full:', 'rebuild-app:', 'rebuild-risk-vllm:', 'remove-plan:']:
        assert target in makefile

    build_doc = (ROOT / 'docs/development/build_ux.md').read_text(encoding='utf-8')
    assert 'build, start, readiness, deploy, release는 서로 다른 동작이다.' in build_doc
    assert '`make build`는 artifact/image를 생성하고 검증한다.' in build_doc
    assert '`make start`는 local service 또는 compose stack을 시작한다.' in build_doc
    assert '`make ready`는 live stack readiness를 증명한다.' in build_doc

    scripts_doc = (ROOT / 'scripts/README.md').read_text(encoding='utf-8')
    assert 'build와 runtime은 다른 단계다' in scripts_doc
    assert '`make build`는 서비스를 시작하지 않는다' in scripts_doc
    assert 'make build-pipeline' in build_doc
    assert 'make first-run' in build_doc
    assert 'make remove-plan' in build_doc
    assert (ROOT / 'docs/operations/first_project_guide.md').exists()
    build_all = (ROOT / "scripts/build/build_all.sh").read_text(encoding="utf-8")
    assert "scripts/reports/refresh_generated_reports.py" in build_all
    assert "scripts/validation/check_docs_links.py" in build_all
    assert "scripts/commands/validate_command_registry.py --strict" in build_all
    assert "docker CLI is required because make build includes the platform image" in build_all
    assert "platform image build skipped" not in build_all
    assert "PACKAGE_SKIP_VALIDATION=1 bash scripts/build/package_release.sh" in build_all


def test_env_bootstrap_and_image_tag_automation_are_present() -> None:
    import os
    setup = ROOT / 'scripts/config/setup_env.py'
    build_image = ROOT / 'scripts/build/build_platform_image.sh'
    build_risk_image = ROOT / 'scripts/build/build_risk_vllm_image.sh'
    check_risk_image = ROOT / 'scripts/models/check_risk_vllm_image_config.sh'
    assert setup.exists()
    assert build_image.exists()
    assert build_risk_image.exists()
    assert check_risk_image.exists()
    assert os.access(setup, os.X_OK)
    assert os.access(build_image, os.X_OK)
    assert os.access(build_risk_image, os.X_OK)
    assert os.access(check_risk_image, os.X_OK)

    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    for target in ['init-env:', 'init-env-local:', 'init-env-compose:', 'show-image-tags:', 'build-image:', 'build-risk-vllm-image:', 'risk-vllm-config-check:', 'compose-up:', 'compose-down:']:
        assert target in makefile

    images = yaml.safe_load((ROOT / 'configs/recommended_images.yaml').read_text(encoding='utf-8'))['images']
    assert images['platform']['default'] == f"ai-model-serving-platform:{(ROOT / 'VERSION').read_text(encoding='utf-8').strip()}"
    assert images['vllm']['default'].startswith('vllm/vllm-openai:gemma4')
    assert images['risk_vllm']['default'].startswith('ai-model-serving-risk-vllm-kanana:')
    assert images['risk_vllm']['compatibility_pins']['transformers'] == '4.52.4'
    assert images['dcgm_exporter']['default'].startswith('nvcr.io/nvidia/k8s/dcgm-exporter:')
    assert images['prometheus']['default'].startswith('prom/prometheus:v3')
    assert images['grafana']['default'].startswith('grafana/grafana:12.2')
    build_script = (ROOT / 'scripts/build/build_risk_vllm_image.sh').read_text(encoding='utf-8')
    assert "print_risk_vllm_compatibility.py" in build_script
    assert 'load_local_env "$ENV_FILE"' in build_script
    assert 'below the Kanana minimum' in build_script
    dockerignore = (ROOT / '.dockerignore').read_text(encoding='utf-8')
    assert 'ops/*' in dockerignore
    assert '!ops/docker/Dockerfile.risk-vllm-kanana' in dockerignore
    assert '!ops/patches/transformers_llama_head_dim_guard.py' in dockerignore


def test_bootstrap_restarts_gateway_and_admin_sidecar_at_the_same_revision() -> None:
    bootstrap = (ROOT / "scripts/build/bootstrap.sh").read_text(encoding="utf-8")
    assert "render_main_model_boot_override.py" in bootstrap
    assert 'docker compose "${_compose_args[@]}" --env-file "$ENV_FILE_ABS" config' in bootstrap
    assert "up -d --no-deps admin-sidecar" in bootstrap
    assert "up -d --no-deps gateway risk-adapter" in bootstrap
    assert "python3.12 python3.13 python3.14" in bootstrap
    assert "Python >=3.12,<3.15 not found" in bootstrap


def test_config_version_semantics_are_explicit() -> None:
    for rel in [
        "configs/model_catalog.yaml",
        "configs/monitoring.yaml",
        "harness/runtime_validation_matrix.yaml",
    ]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "version_semantics:" in text
        assert "not package release version" in text


def test_embedding_ko_vllm_uses_shared_vllm_image_without_local_build() -> None:
    """Korean dense embedding runtime uses the standard vLLM image and has no custom build artifact."""
    for rel in [
        "ops/compose/full-stack.private-network.yaml",
    ]:
        raw = (ROOT / rel).read_text(encoding="utf-8")
        assert "embedding-ko-vllm" in raw
        assert "COLBERT_KO" not in raw
        assert "Dockerfile.colbert-ko-vllm" not in raw


def test_clean_removes_timestamped_runtime_reports_but_keeps_shared_state() -> None:
    clean = (ROOT / "scripts/ops/clean_all.sh").read_text(encoding="utf-8")
    assert "remove_runtime_validation_reports" in clean
    assert "runtime_validation_*.json" in clean
    assert "runtime_validation_*.md" in clean
    assert 'PURGE_MODEL_CACHE:-0' in clean
    assert 'PURGE_RUNTIME_SECRETS:-0' in clean
