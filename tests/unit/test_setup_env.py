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
    assert 'EXPOSURE_MODE=master_open' in text
    assert 'EXPOSURE_AUDIENCE=private_lan' in text
    assert 'ADMIN_API_KEY=ams_admin_' in text
    assert 'ADMIN_API_KEYS=ams_admin_' in text
    assert 'API_KEYS=ams_gateway_' in text
    assert 'API_KEY=ams_gateway_' in text
    assert 'INTERNAL_SERVICE_TOKEN=ams_internal_' in text
    assert 'FASTAPI_DOCS_ENABLED=true' in text
    assert 'VLLM_IMAGE=ai-model-serving-vllm-unified:' in text
    assert 'RISK_VLLM_IMAGE=ai-model-serving-vllm-unified:' in text
    # MAX_REQUEST_BODY_BYTES is yaml-owned; it must not be an active .env assignment.
    assert not any(line.startswith('MAX_REQUEST_BODY_BYTES=') for line in text.splitlines())
    assert 'COLBERT_KO_MODEL_DIR' not in text
    assert 'PROMETHEUS_IMAGE=prom/prometheus:v3-distroless' in text
    assert 'LOKI_IMAGE=grafana/loki:' in text
    assert 'PROMTAIL_IMAGE=grafana/promtail:' in text


def test_setup_env_generates_local_open_profile(tmp_path):
    out = tmp_path / '.env'
    rc = setup_env.main(['--profile', 'local', '--output', str(out)])
    assert rc == 0
    text = out.read_text(encoding='utf-8')
    assert 'APP_ENV=local' in text
    assert 'AUTH_MODE=local_open' in text
    assert 'EXPOSURE_MODE=master_open' in text
    assert 'EXPOSURE_AUDIENCE=private_lan' in text
    assert 'API_KEY_REQUIRED=false' in text
    assert 'ADMIN_API_KEY_REQUIRED=false' in text
    assert 'INTERNAL_SERVICE_AUTH_REQUIRED=false' in text
    assert 'FASTAPI_DOCS_ENABLED=true' in text
    assert not any(line.startswith('MAX_REQUEST_BODY_BYTES=') for line in text.splitlines())


def test_setup_env_refuses_overwrite_without_force(tmp_path):
    out = tmp_path / '.env'
    out.write_text('EXISTING=1\n', encoding='utf-8')
    rc = setup_env.main(['--profile', 'local', '--output', str(out)])
    assert rc == 2
    assert out.read_text(encoding='utf-8') == 'EXISTING=1\n'


def test_setup_env_rejects_private_exposure_with_local_open(tmp_path, capsys):
    out = tmp_path / '.env'
    rc = setup_env.main(
        [
            '--profile',
            'compose',
            '--output',
            str(out),
            '--exposure-mode',
            'private_network',
        ]
    )
    assert rc == 2
    assert "AUTH_MODE=local_open requires" in capsys.readouterr().err


def test_setup_env_force_rejects_duplicate_existing_env(tmp_path, capsys):
    out = tmp_path / '.env'
    out.write_text('AUTH_MODE=local_open\nAUTH_MODE=strict\n', encoding='utf-8')

    rc = setup_env.main(['--profile', 'compose', '--output', str(out), '--force'])

    assert rc == 2
    assert "duplicate env key 'AUTH_MODE'" in capsys.readouterr().err


def test_setup_env_sync_rejects_quoted_existing_env(tmp_path, capsys):
    out = tmp_path / '.env'
    out.write_text('BUILD_PROFILE=compose\nEXPOSURE_MODE="master_open"\n', encoding='utf-8')

    rc = setup_env.main(['--sync-env', '--env-file', str(out)])

    assert rc == 2
    assert "quoted values are not supported" in capsys.readouterr().err


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


def test_setup_env_force_keeps_risk_vllm_image_equal_to_main_image(tmp_path):
    # 2026-07-24부터 VLLM_IMAGE와 RISK_VLLM_IMAGE가 같은 vLLM unified 이미지를
    # 가리키는 게 정상 상태다(Gemma4 멀티모달 + Kanana head_dim 패치가 한
    # 이미지에 같이 있고, 각 patch는 무관한 모델에는 no-op이다). 예전엔 이 상태를
    # "shared/base image로의 실수"로 보고 강제로 되돌렸는데, 이제는 그대로 둔다.
    out = tmp_path / '.env'
    shared = 'gitlab.example.com/registry/vllm-unified:custom'
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
    assert f'RISK_VLLM_IMAGE={shared}' in text
    assert 'HF_TOKEN=hf_existing' in text


def test_setup_env_fills_default_risk_vllm_image_when_unset(tmp_path):
    out = tmp_path / '.env'
    out.write_text('HF_TOKEN=hf_existing\n', encoding='utf-8')
    rc = setup_env.main(['--profile', 'compose', '--output', str(out), '--force'])
    assert rc == 0
    text = out.read_text(encoding='utf-8')
    assert 'RISK_VLLM_IMAGE=ai-model-serving-vllm-unified:' in text


def test_setup_env_force_omits_max_request_body_bytes(tmp_path):
    # MAX_REQUEST_BODY_BYTES is yaml-owned (model_serving.yaml). It must not be written
    # as an active .env assignment, so it can never shadow the yaml value again.
    out = tmp_path / '.env'
    assert setup_env.main(['--profile', 'compose', '--output', str(out), '--force']) == 0
    lines = set(out.read_text(encoding='utf-8').splitlines())
    assert not any(
        line.startswith('MAX_REQUEST_BODY_BYTES=') for line in lines
    )


def test_sync_env_retires_max_request_body_bytes(tmp_path):
    # An existing .env carried over from older templates still has the key; sync-env must
    # strip it so the yaml value (image-baked / rsynced) becomes authoritative. This is the
    # deploy path that left /opt/acl-ai-gateway/.env stuck at 1.25 MB across releases.
    out = tmp_path / '.env'
    assert setup_env.main(['--profile', 'compose', '--output', str(out), '--force']) == 0
    with out.open('a', encoding='utf-8') as fh:
        fh.write('MAX_REQUEST_BODY_BYTES=1250000\n')

    rc = setup_env.main(['--sync-env', '--env-file', str(out)])

    assert rc == 0
    lines = set(out.read_text(encoding='utf-8').splitlines())
    assert not any(line.startswith('MAX_REQUEST_BODY_BYTES=') for line in lines)


def test_sync_env_no_op_when_already_current(tmp_path, capsys):
    out = tmp_path / '.env'
    assert setup_env.main(['--profile', 'compose', '--output', str(out), '--force']) == 0

    rc = setup_env.main(['--sync-env', '--env-file', str(out)])

    assert rc == 0
    assert '변경 없음' in capsys.readouterr().out


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
