from __future__ import annotations

import shutil

from scripts.config import setup_env


def test_setup_env_generates_compose_env_with_local_open_defaults(tmp_path):
    out = tmp_path / '.env'
    rc = setup_env.main(['--profile', 'compose', '--output', str(out)])
    assert rc == 0
    text = out.read_text(encoding='utf-8')
    assert 'APP_ENV=local' in text
    assert 'API_KEY_REQUIRED=false' in text
    assert 'ADMIN_API_KEY_REQUIRED=false' in text
    assert 'INTERNAL_SERVICE_AUTH_REQUIRED=false' in text
    assert 'AUTH_MODE=local_open' in text
    assert 'ADMIN_API_KEY=ams_admin_' in text
    assert 'ADMIN_API_KEYS=ams_admin_' in text
    assert 'API_KEYS=ams_gateway_' in text
    assert 'API_KEY=ams_gateway_' in text
    assert 'INTERNAL_SERVICE_TOKEN=ams_internal_' in text
    assert 'FASTAPI_DOCS_ENABLED=true' in text
    assert 'VLLM_IMAGE=vllm/vllm-openai:gemma4-0505-cu129' in text
    assert 'RISK_VLLM_IMAGE=ai-model-serving-risk-vllm-kanana:' in text
    assert 'COLBERT_KO_MODEL_DIR=./models/colbert-ko-vllm' in text
    assert 'PROMETHEUS_IMAGE=prom/prometheus:v3-distroless' in text


def test_setup_env_generates_local_open_profile(tmp_path):
    out = tmp_path / '.env'
    rc = setup_env.main(['--profile', 'local', '--output', str(out)])
    assert rc == 0
    text = out.read_text(encoding='utf-8')
    assert 'APP_ENV=local' in text
    assert 'AUTH_MODE=local_open' in text
    assert 'API_KEY_REQUIRED=false' in text
    assert 'ADMIN_API_KEY_REQUIRED=false' in text
    assert 'INTERNAL_SERVICE_AUTH_REQUIRED=false' in text
    assert 'FASTAPI_DOCS_ENABLED=true' in text


def test_setup_env_refuses_overwrite_without_force(tmp_path):
    out = tmp_path / '.env'
    out.write_text('EXISTING=1\n', encoding='utf-8')
    rc = setup_env.main(['--profile', 'local', '--output', str(out)])
    assert rc == 2
    assert out.read_text(encoding='utf-8') == 'EXISTING=1\n'


def test_setup_env_show_image_tags(capsys):
    rc = setup_env.main(['--show-image-tags'])
    assert rc == 0
    captured = capsys.readouterr().out
    assert 'vllm:' in captured
    assert 'risk_vllm:' in captured
    assert 'grafana:' in captured


def test_setup_env_preserves_operator_values_on_force_but_rotates_generated_secrets(tmp_path):
    out = tmp_path / '.env'
    out.write_text(
        'HF_TOKEN=hf_existing\n'
        'GATEWAY_PORT=19500\n'
        'PLATFORM_IMAGE=custom/platform:dev\n'
        'API_KEYS=old-secret\n',
        encoding='utf-8',
    )
    rc = setup_env.main(['--profile', 'compose', '--output', str(out), '--force'])
    assert rc == 0
    text = out.read_text(encoding='utf-8')
    assert 'HF_TOKEN=hf_existing' in text
    assert 'HUGGING_FACE_HUB_TOKEN=hf_existing' in text
    assert 'GATEWAY_PORT=19500' in text
    assert 'PLATFORM_IMAGE=custom/platform:dev' in text
    assert 'API_KEYS=ams_gateway_' in text
    assert 'API_KEYS=old-secret' not in text


def test_setup_env_force_migrates_stale_shared_risk_vllm_image(tmp_path):
    out = tmp_path / '.env'
    shared = 'vllm/vllm-openai:gemma4-0505-cu129'
    out.write_text(
        f'VLLM_IMAGE={shared}\n'
        f'RISK_VLLM_IMAGE={shared}\n'
        'HF_TOKEN=hf_existing\n',
        encoding='utf-8',
    )
    rc = setup_env.main(['--profile', 'compose', '--output', str(out), '--force'])
    assert rc == 0
    text = out.read_text(encoding='utf-8')
    assert f'VLLM_IMAGE={shared}' in text
    assert f'RISK_VLLM_IMAGE={shared}' not in text
    assert 'RISK_VLLM_IMAGE=ai-model-serving-risk-vllm-kanana:' in text
    assert 'HF_TOKEN=hf_existing' in text


def test_setup_env_force_migrates_stale_risk_vllm_transformers_pin(tmp_path):
    out = tmp_path / '.env'
    out.write_text(
        'RISK_VLLM_IMAGE=ai-model-serving-risk-vllm-kanana:0.1.0-rc.1\n'
        'RISK_VLLM_TRANSFORMERS_VERSION=4.51.3\n'
        'HF_TOKEN=hf_existing\n',
        encoding='utf-8',
    )
    rc = setup_env.main(['--profile', 'compose', '--output', str(out), '--force'])
    assert rc == 0
    text = out.read_text(encoding='utf-8')
    assert 'RISK_VLLM_TRANSFORMERS_VERSION=4.52.4' in text
    assert 'HF_TOKEN=hf_existing' in text


def test_setup_env_force_removes_retired_risk_siren_keys(tmp_path):
    out = tmp_path / '.env'
    out.write_text(
        'RISK_SIREN_BASE_URL=http://risk-siren-vllm:9404/v1\n'
        'RISK_SIREN_MODEL=risk-siren\n'
        'RISK_SIREN_TIMEOUT_SECONDS=5\n'
        'RISK_SIREN_MAX_CONCURRENCY=1\n'
        'RISK_SIREN_QUEUE_TIMEOUT_SECONDS=2\n'
        'HF_TOKEN=hf_existing\n',
        encoding='utf-8',
    )
    rc = setup_env.main(['--profile', 'compose', '--output', str(out), '--force'])
    assert rc == 0
    text = out.read_text(encoding='utf-8')
    assert 'RISK_SIREN_' not in text
    assert 'HF_TOKEN=hf_existing' in text


def test_setup_env_force_preserves_custom_risk_vllm_image(tmp_path):
    out = tmp_path / '.env'
    out.write_text(
        'VLLM_IMAGE=vllm/vllm-openai:gemma4-0505-cu129\n'
        'RISK_VLLM_IMAGE=registry.example.com/custom/kanana-risk:dev\n',
        encoding='utf-8',
    )
    rc = setup_env.main(['--profile', 'compose', '--output', str(out), '--force'])
    assert rc == 0
    text = out.read_text(encoding='utf-8')
    assert 'RISK_VLLM_IMAGE=registry.example.com/custom/kanana-risk:dev' in text


def test_setup_env_syncs_runtime_secret_from_existing_env(tmp_path, monkeypatch):
    monkeypatch.chdir(setup_env.ROOT)
    env_path = tmp_path / '.env'
    env_path.write_text('ADMIN_API_KEY=admin-from-env\n', encoding='utf-8')
    secret_path = setup_env.ROOT / '.runtime' / 'prometheus' / 'admin_api_key'
    if secret_path.exists():
        if secret_path.is_dir():
            shutil.rmtree(secret_path)
        else:
            secret_path.unlink()
    rc = setup_env.main(['--sync-runtime-secrets', '--output', str(env_path)])
    assert rc == 0
    assert secret_path.read_text(encoding='utf-8') == 'admin-from-env\n'
    assert secret_path.stat().st_mode & 0o777 == 0o644


def test_setup_env_repairs_empty_runtime_secret_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(setup_env.ROOT)
    env_path = tmp_path / '.env'
    env_path.write_text('ADMIN_API_KEY=admin-from-env\n', encoding='utf-8')
    secret_path = setup_env.ROOT / '.runtime' / 'prometheus' / 'admin_api_key'
    if secret_path.exists():
        if secret_path.is_dir():
            shutil.rmtree(secret_path)
        else:
            secret_path.unlink()
    secret_path.mkdir(parents=True)

    rc = setup_env.main(['--sync-runtime-secrets', '--output', str(env_path)])

    assert rc == 0
    assert secret_path.is_file()
    assert secret_path.read_text(encoding='utf-8') == 'admin-from-env\n'
    assert secret_path.stat().st_mode & 0o777 == 0o644


def test_setup_env_refuses_non_empty_runtime_secret_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(setup_env.ROOT)
    env_path = tmp_path / '.env'
    env_path.write_text('ADMIN_API_KEY=admin-from-env\n', encoding='utf-8')
    secret_path = setup_env.ROOT / '.runtime' / 'prometheus' / 'admin_api_key'
    if secret_path.exists():
        if secret_path.is_dir():
            shutil.rmtree(secret_path)
        else:
            secret_path.unlink()
    secret_path.mkdir(parents=True)
    (secret_path / 'unexpected').write_text('keep-me\n', encoding='utf-8')

    rc = setup_env.main(['--sync-runtime-secrets', '--output', str(env_path)])

    assert rc == 2
    assert 'must be a file, but it is a non-empty directory' in capsys.readouterr().err
    shutil.rmtree(secret_path)


def test_reset_version_updates_recommended_platform_image(tmp_path):
    from scripts.build import reset_version

    config = tmp_path / 'recommended_images.yaml'
    config.write_text('images:\n  platform:\n    default: ai-model-serving-platform:0.1.13\n', encoding='utf-8')
    reset_version.replace_platform_image_tag(config, '9.8.7')
    assert 'ai-model-serving-platform:9.8.7' in config.read_text(encoding='utf-8')


def test_reset_version_converts_rc_to_pep440_package_version():
    from scripts.build import reset_version

    assert reset_version.python_package_version('0.1.0-rc.1') == '0.1.0rc1'
    assert reset_version.python_package_version('0.1.0') == '0.1.0'
    assert reset_version.is_valid_project_version('0.1.0-rc.1') is True
    assert reset_version.is_valid_project_version('0.1.0rc1') is False
