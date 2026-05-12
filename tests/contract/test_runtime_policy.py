from __future__ import annotations

import csv
import json
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def python_package_version(version: str) -> str:
    if '-rc.' in version:
        base, rc = version.split('-rc.', 1)
        return f'{base}rc{rc}'
    return version


def test_python_default_and_supported_range_are_aligned() -> None:
    assert (ROOT / '.python-version').read_text(encoding='utf-8').strip() == '3.12.13'
    pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    assert pyproject['project']['requires-python'] == '>=3.12,<3.15'
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    assert pyproject['project']['version'] == python_package_version(version)
    compat = yaml.safe_load((ROOT / 'configs/runtime_compatibility.yaml').read_text(encoding='utf-8'))
    assert compat['python']['default_version'] == '3.12.13'
    assert compat['python']['supported_range'] == '>=3.12,<3.15'


def test_monitoring_ports_and_privacy_settings_are_aligned() -> None:
    monitoring = yaml.safe_load((ROOT / 'configs/monitoring.yaml').read_text(encoding='utf-8'))
    ports = yaml.safe_load((ROOT / 'configs/ports.yaml').read_text(encoding='utf-8'))['ports']
    assert monitoring['monitoring_stack']['prometheus']['port'] == ports['prometheus'] == 9410
    assert monitoring['monitoring_stack']['grafana']['port'] == ports['grafana'] == 9411
    assert monitoring['monitoring_stack']['dcgm_exporter']['port'] == ports['dcgm_exporter'] == 9412
    assert monitoring['monitoring_stack']['cadvisor']['port'] == ports['cadvisor'] == 9413
    assert monitoring['metric_sources']['vllm_instances']['label_policy']['model'].startswith('logical served model name')
    assert monitoring['metric_sources']['vllm_containers']['compose_service_label'] == 'container_label_com_docker_compose_service'
    privacy = monitoring['privacy_and_security']
    assert privacy['raw_prompt_in_metrics'] == 'forbidden'
    assert privacy['user_text_labels'] == 'forbidden'
    assert privacy['model_output_text_labels'] == 'forbidden'


def test_version_metadata_records_current_package_metadata() -> None:
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    manifest = json.loads((ROOT / 'version_manifest.json').read_text(encoding='utf-8'))
    assert re.fullmatch(r'\d+\.\d+\.\d+(-rc\.\d+)?', version), f'unexpected VERSION format: {version}'
    assert manifest['version'] == version
    assert manifest['package_profile'] == 'platform'
    assert manifest['package_contract']['scope'] == 'platform service package'
    assert (ROOT / 'src/ai_model_serving/apps/gateway.py').exists()
    assert (ROOT / 'src/ai_model_serving/apps/risk_adapter.py').exists()


def test_model_source_facts_and_runtime_policy_are_separated() -> None:
    catalog = yaml.safe_load((ROOT / 'configs/model_catalog.yaml').read_text(encoding='utf-8'))['models']
    main = catalog['local-main']
    assert main['source_facts']['upstream_example']['tensor_parallel_size'] == 1
    assert main['source_facts']['upstream_example']['max_model_len'] == 16384
    assert main['project_runtime_policy']['tensor_parallel_size'] == 1
    assert main['project_runtime_policy']['max_model_len'] == 16384
    assert main['project_runtime_policy']['max_image_inputs'] == 1
    assert main['project_runtime_policy']['max_image_bytes'] == 750000
    assert set(main['project_runtime_policy']['allowed_image_mime_types']) == {'image/jpeg', 'image/png', 'image/webp'}
    prompt = catalog['risk-prompt']
    assert prompt['source_facts']['model_card_max_new_tokens'] == 1
    for rel in ['local-main', 'local-embed', 'risk-prompt']:
        card = json.loads((ROOT / f'model_cards/{rel}.json').read_text(encoding='utf-8'))
        assert ('validation' + '_status') not in card
        assert 'source_facts' in card
        assert 'project_runtime_policy' in card


def test_api_contract_matrix_has_auth_schema_and_exposure_columns() -> None:
    with (ROOT / 'contracts/api_contract_matrix.csv').open(encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    required_columns = {'auth_required', 'admin_auth', 'exposure', 'request_schema', 'response_schema'}
    assert required_columns.issubset(rows[0].keys())
    assert any(row['public_endpoint'] == '/ready' and row['exposure'] == 'internal_only' for row in rows)
    assert all(row['policy_action'] == 'not defined' for row in rows)
    admin_protected = [row for row in rows if row['admin_auth'] == 'conditional(ADMIN_API_KEY_REQUIRED)']
    assert {row['public_endpoint'] for row in admin_protected} == {'/ready', '/metrics'}
    assert all(row['auth_required'] == 'false' for row in admin_protected)


def test_common_error_codes_are_enumerated() -> None:
    schema = json.loads((ROOT / 'specs/schemas/common_error.schema.json').read_text(encoding='utf-8'))
    codes = set(schema['properties']['error']['properties']['code']['enum'])
    assert {'VALIDATION_ERROR', 'MODEL_UNAVAILABLE', 'UPSTREAM_TIMEOUT', 'UPSTREAM_SCHEMA_ERROR', 'RUNTIME_NOT_READY'}.issubset(codes)


def test_model_catalog_and_model_contracts_are_cross_checked() -> None:
    catalog = yaml.safe_load((ROOT / 'configs/model_catalog.yaml').read_text(encoding='utf-8'))['models']
    contracts = yaml.safe_load((ROOT / 'contracts/model_contracts.yaml').read_text(encoding='utf-8'))['models']
    assert set(contracts) == set(catalog) == {'local-main', 'local-embed', 'risk-prompt'}
    for logical_id, cfg in catalog.items():
        runtime = cfg['runtime']
        assert contracts[logical_id]['port'] == runtime['port']
        listing = cfg['gateway_listing']
        assert listing['enabled'] is True
        assert listing['backend']
        assert listing['capabilities']


def test_runtime_lockfile_and_dockerfile_hardening_are_present() -> None:
    runtime_lock = ROOT / 'requirements.runtime.lock'
    contract_lock = ROOT / 'requirements.lock'
    assert runtime_lock.exists()
    assert contract_lock.exists()
    runtime_lock_text = runtime_lock.read_text(encoding='utf-8')
    assert 'fastapi==' in runtime_lock_text
    assert 'uvicorn==' in runtime_lock_text
    assert 'httpx==' in runtime_lock_text
    assert 'pytest==' not in runtime_lock_text
    assert 'jsonschema==' not in runtime_lock_text
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert 'FROM python:3.12.13-slim' in dockerfile
    assert '--requirement requirements.runtime.lock' in dockerfile
    assert 'USER appuser' in dockerfile
    assert 'HEALTHCHECK' in dockerfile
    assert '/health' in dockerfile


def test_user_facing_version_references_are_aligned() -> None:
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    assert f'패키지 버전 | `{version}`' in (ROOT / 'README.md').read_text(encoding='utf-8')
    assert f'PROJECT_VERSION={version}' in (ROOT / '.env.example').read_text(encoding='utf-8')
    assert f'PROJECT_VERSION={version}' in (ROOT / '.env.local.example').read_text(encoding='utf-8')
    assert f'PROJECT_VERSION={version}' in (ROOT / '.env.compose.example').read_text(encoding='utf-8')
    policy = (ROOT / 'docs/release/versioning_policy.md').read_text(encoding='utf-8')
    assert version in policy
    assert python_package_version(version) in policy


def test_main_runtime_features_and_request_parameter_policy_are_explicit() -> None:
    serving = yaml.safe_load((ROOT / 'configs/model_serving.yaml').read_text(encoding='utf-8'))['models']
    main = serving['main_llm']
    features = main['runtime_features']
    assert features['prefix_caching']['enabled'] is True
    assert features['prefix_caching']['hash_algo'] == 'sha256_cbor'
    assert features['tool_calling']['enabled'] is True
    assert features['tool_calling']['tool_call_parser'] == 'gemma4'
    assert 'reasoning_parser' not in features['tool_calling']
    assert features['tool_calling']['chat_template'] == '/app/configs/gemma4_chat_template.jinja'
    policy = main['request_parameter_policy']
    assert policy['allow_unlisted_parameters'] is False
    for field in ['top_p', 'top_k', 'min_p', 'repetition_penalty', 'tools', 'tool_choice']:
        assert field in policy['supported_parameters']

    for key in ['risk_prompt']:
        assert serving[key]['runtime_features']['tool_calling']['enabled'] is False
        assert serving[key]['runtime_features']['prefix_caching']['enabled'] is False


def _compose_command_args(service_name: str) -> dict[str, str | bool]:
    compose = yaml.safe_load((ROOT / 'ops/compose/full-stack.example.yaml').read_text(encoding='utf-8'))
    command = compose['services'][service_name]['command']
    result: dict[str, str | bool] = {}
    i = 0
    while i < len(command):
        token = command[i]
        if isinstance(token, str) and token.startswith('--'):
            key = token[2:].replace('-', '_')
            if i + 1 < len(command) and isinstance(command[i + 1], str) and not command[i + 1].startswith('--'):
                result[key] = command[i + 1]
                i += 2
            else:
                result[key] = True
                i += 1
        else:
            i += 1
    return result


def test_embedding_pooling_runtime_has_valid_batch_token_budget() -> None:
    serving = yaml.safe_load((ROOT / 'configs/model_serving.yaml').read_text(encoding='utf-8'))['models']['embedding']
    catalog = yaml.safe_load((ROOT / 'configs/model_catalog.yaml').read_text(encoding='utf-8'))['models']['local-embed']
    card = json.loads((ROOT / 'model_cards/local-embed.json').read_text(encoding='utf-8'))
    args = _compose_command_args('embedding-vllm')

    assert serving['max_model_len'] == 2048
    assert serving['max_num_batched_tokens'] >= serving['max_model_len']
    assert catalog['project_runtime_policy']['max_num_batched_tokens'] == serving['max_num_batched_tokens']
    assert card['project_runtime_policy']['max_num_batched_tokens'] == serving['max_num_batched_tokens']
    assert int(args['max_num_batched_tokens']) == serving['max_num_batched_tokens']
    assert int(args['max_num_batched_tokens']) >= int(args['max_model_len'])


def test_risk_detector_quantization_defaults_are_preserved() -> None:
    serving = yaml.safe_load((ROOT / 'configs/model_serving.yaml').read_text(encoding='utf-8'))['models']
    for service_name, key in [('risk-prompt-vllm', 'risk_prompt')]:
        args = _compose_command_args(service_name)
        assert args['quantization'] == 'bitsandbytes'
        assert args['load_format'] == 'bitsandbytes'
        assert serving[key]['quantization'] == 'bitsandbytes'
        assert serving[key]['load_format'] == 'bitsandbytes'
        assert serving[key]['max_output_tokens'] == 1


def test_kanana_risk_config_shape_facts_are_recorded() -> None:
    catalog = yaml.safe_load((ROOT / 'configs/model_catalog.yaml').read_text(encoding='utf-8'))['models']
    prompt_shape = catalog['risk-prompt']['source_facts']['config_shape']

    assert prompt_shape['model_type'] == 'llama'
    assert prompt_shape['architecture'] == 'LlamaForCausalLM'
    assert prompt_shape['hidden_size'] == 1792
    assert prompt_shape['num_attention_heads'] == 24
    assert prompt_shape['head_dim'] == 128
    assert prompt_shape['hidden_size_divisible_by_attention_heads'] is False
    assert prompt_shape['attention_projection_width'] == 3072
    assert prompt_shape['requires_runtime_head_dim_support'] is True
