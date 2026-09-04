"""scripts/config/setup_env.py(.env 최초 생성/동기화 CLI)를 검증한다: 프로필별
기본값 생성, 강제 덮어쓰기 시 운영자 값 보존과 secret 로테이션, YAML 소유 설정의
중복 env override 제거, runtime secret 파일 동기화/복구."""

from __future__ import annotations

from scripts.config import setup_env


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


def test_setup_env_force_removes_registered_env_overrides(tmp_path):
    # yaml이 소유하는 운영 한도에 오래된 .env 값이 남으면 yaml 변경을 조용히
    # 가릴 수 있다. --force 경로도 sync 경로와 같은 목록을 지우는지만 본다.
    out = tmp_path / '.env'
    out.write_text(
        ''.join(f'{key}=placeholder\n' for key in setup_env.REMOVED_ENV_KEYS)
        + 'HF_TOKEN=hf_existing\n',
        encoding='utf-8',
    )
    rc = setup_env.main(['--profile', 'compose', '--output', str(out), '--force'])
    assert rc == 0
    text = out.read_text(encoding='utf-8')
    for key in setup_env.REMOVED_ENV_KEYS:
        assert not any(line.startswith(f'{key}=') for line in text.splitlines()), key
    assert 'HF_TOKEN=hf_existing' in text


def test_sync_env_removes_only_registered_keys_and_keeps_server_only_settings(tmp_path):
    """sync-env의 제거 기준은 "등록된 키"이지 "템플릿에 없는 키"가 아니다.

    deploy_gitlab_compose.sh가 이미지 참조 갱신 직후 이 경로를 호출하므로, 제거
    기준이 템플릿 유무로 바뀌면 배포 서버에만 존재하는 운영 설정(상태 파일 경로 등)이
    배포할 때마다 사라진다. 등록된 키는 지우고 나머지 값은 건드리지 않는다는 두 방향을
    함께 고정한다.
    """
    out = tmp_path / '.env'
    out.write_text(
        'BUILD_PROFILE=compose\n'
        # 템플릿에 없지만 서버가 실제로 쓰는 설정 -- 보존되어야 한다.
        'MAIN_MODEL_STATE_PATH=/app/.runtime/main-model/main-model-state.json\n'
        'SECRETS_GENERATED_AT=2026-05-11T07:33:08Z\n'
        'HF_TOKEN=hf_existing\n'
        + ''.join(f'{key}=placeholder\n' for key in setup_env.REMOVED_ENV_KEYS),
        encoding='utf-8',
    )

    rc = setup_env.main(['--sync-env', '--env-file', str(out)])

    assert rc == 0
    lines = out.read_text(encoding='utf-8').splitlines()
    for key in setup_env.REMOVED_ENV_KEYS:
        assert not any(line.startswith(f'{key}=') for line in lines), key
    assert 'MAIN_MODEL_STATE_PATH=/app/.runtime/main-model/main-model-state.json' in lines
    assert 'SECRETS_GENERATED_AT=2026-05-11T07:33:08Z' in lines
    assert 'HF_TOKEN=hf_existing' in lines
    assert 'DEPLOYMENT_TARGET=linux-nvidia-dynamic' in lines
    assert 'MAIN_LLM_STATIC_PROFILE=gemma4-12b-unified-fp8' in lines


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
    monkeypatch.setattr(setup_env, "ROOT", tmp_path)
    env_path = tmp_path / '.env'
    env_path.write_text('ADMIN_API_KEY=admin-from-env\n', encoding='utf-8')
    secret_path = tmp_path / '.runtime' / 'prometheus' / 'admin_api_key'
    rc = setup_env.main(['--sync-runtime-secrets', '--output', str(env_path)])
    assert rc == 0
    assert secret_path.read_text(encoding='utf-8') == 'admin-from-env\n'
    assert secret_path.stat().st_mode & 0o777 == 0o644


def test_setup_env_repairs_empty_runtime_secret_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_env, "ROOT", tmp_path)
    env_path = tmp_path / '.env'
    env_path.write_text('ADMIN_API_KEY=admin-from-env\n', encoding='utf-8')
    secret_path = tmp_path / '.runtime' / 'prometheus' / 'admin_api_key'
    secret_path.mkdir(parents=True)

    rc = setup_env.main(['--sync-runtime-secrets', '--output', str(env_path)])

    assert rc == 0
    assert secret_path.is_file()
    assert secret_path.read_text(encoding='utf-8') == 'admin-from-env\n'
    assert secret_path.stat().st_mode & 0o777 == 0o644


def test_setup_env_refuses_non_empty_runtime_secret_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(setup_env, "ROOT", tmp_path)
    env_path = tmp_path / '.env'
    env_path.write_text('ADMIN_API_KEY=admin-from-env\n', encoding='utf-8')
    secret_path = tmp_path / '.runtime' / 'prometheus' / 'admin_api_key'
    secret_path.mkdir(parents=True)
    (secret_path / 'unexpected').write_text('keep-me\n', encoding='utf-8')

    rc = setup_env.main(['--sync-runtime-secrets', '--output', str(env_path)])

    assert rc == 2
    assert 'must be a file, but it is a non-empty directory' in capsys.readouterr().err
