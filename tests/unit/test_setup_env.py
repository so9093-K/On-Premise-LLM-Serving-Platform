"""scripts/config/setup_env.py(.env 최초 생성/동기화 CLI)를 검증한다: 프로필별
기본값 생성, 강제 덮어쓰기 시 운영자 값 보존과 secret 로테이션, 폐기된 키
일괄 제거, runtime secret 파일 동기화/복구."""

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
    # MAX_REQUEST_BODY_BYTES는 yaml이 소유한다; .env에 활성 할당으로 남아있으면 안 된다.
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


def test_setup_env_fills_default_risk_vllm_image_when_unset(tmp_path):
    out = tmp_path / '.env'
    out.write_text('HF_TOKEN=hf_existing\n', encoding='utf-8')
    rc = setup_env.main(['--profile', 'compose', '--output', str(out), '--force'])
    assert rc == 0
    text = out.read_text(encoding='utf-8')
    assert 'RISK_VLLM_IMAGE=ai-model-serving-vllm-unified:' in text


def test_sync_env_no_op_when_already_current(tmp_path, capsys):
    out = tmp_path / '.env'
    assert setup_env.main(['--profile', 'compose', '--output', str(out), '--force']) == 0

    rc = setup_env.main(['--sync-env', '--env-file', str(out)])

    assert rc == 0
    assert '변경 없음' in capsys.readouterr().out


def test_setup_env_force_removes_every_retired_key(tmp_path):
    # 특정 사고 하나(예: risk-siren 제거)의 키만 하드코딩하면 RETIRED_ENV_KEYS에
    # 새 키가 추가될 때마다 이 테스트를 또 늘려야 한다 -- 실제 집합을 순회해서
    # 어떤 키가 들어와도 일반적으로 걸러지는지를 증명한다.
    out = tmp_path / '.env'
    out.write_text(
        ''.join(f'{key}=placeholder\n' for key in setup_env.RETIRED_ENV_KEYS)
        + 'HF_TOKEN=hf_existing\n',
        encoding='utf-8',
    )
    rc = setup_env.main(['--profile', 'compose', '--output', str(out), '--force'])
    assert rc == 0
    text = out.read_text(encoding='utf-8')
    for key in setup_env.RETIRED_ENV_KEYS:
        assert not any(line.startswith(f'{key}=') for line in text.splitlines()), key
    assert 'HF_TOKEN=hf_existing' in text
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
