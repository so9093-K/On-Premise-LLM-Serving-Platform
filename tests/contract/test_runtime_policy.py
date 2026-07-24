from __future__ import annotations

import ast
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
    services = yaml.safe_load((ROOT / 'configs/services.yaml').read_text(encoding='utf-8'))['services']
    ports = {name: service['default_host_port'] for name, service in services.items()}
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
    serving = yaml.safe_load((ROOT / 'configs/model_serving.yaml').read_text(encoding='utf-8'))['models']
    main = catalog['local-main']
    serving_main_limits = serving['main_llm']['resource_control']['request_limits']
    assert main['source_facts']['upstream_example']['tensor_parallel_size'] == 1
    assert main['source_facts']['upstream_example']['max_model_len'] == 96000
    assert main['source_facts']['upstream_context_length_tokens'] == {
        'vllm_recipe': 131072,
        'redhat_card_example_max_model_len': 96000,
        'project_runtime_cap': 20000,
    }
    assert main['source_facts']['parameter_summary'] == {
        'vllm_recipe': '26B total / 4B active',
        'hf_display': '27B params',
    }
    assert main['project_runtime_policy']['tensor_parallel_size'] == 1
    assert main['project_runtime_policy']['max_model_len'] == 20000
    assert main['project_runtime_policy']['max_num_batched_tokens'] == 20000
    assert main['project_runtime_policy']['optimization_level'] == 3
    assert main['project_runtime_policy']['gpu_memory_utilization'] == 0.76
    assert main['project_runtime_policy']['max_image_inputs'] == 1
    assert set(main['project_runtime_policy']['allowed_image_mime_types']) == set(serving_main_limits['allowed_image_mime_types'])
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
    from ai_model_serving.errors import ERROR_STATUS

    schema = json.loads((ROOT / 'specs/schemas/common_error.schema.json').read_text(encoding='utf-8'))
    codes = set(schema['properties']['error']['properties']['code']['enum'])
    assert set(ERROR_STATUS) == codes
    assert {'VALIDATION_ERROR', 'MODEL_UNAVAILABLE', 'UPSTREAM_TIMEOUT', 'UPSTREAM_SCHEMA_ERROR', 'RUNTIME_NOT_READY', 'DETECTOR_DISABLED', 'STREAM_LIMIT_EXCEEDED'}.issubset(codes)


def _service_error_codes_from_source() -> set[str]:
    codes: set[str] = set()
    for path in (ROOT / 'src' / 'ai_model_serving').rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_service_error = (
                isinstance(func, ast.Name) and func.id == 'ServiceError'
            ) or (
                isinstance(func, ast.Attribute) and func.attr == 'ServiceError'
            )
            if not is_service_error or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                codes.add(first.value)
    return codes


def _common_error_enums(document: object) -> list[set[str]]:
    enums: list[set[str]] = []
    if isinstance(document, dict):
        error = document.get('properties', {}).get('error') if isinstance(document.get('properties'), dict) else None
        if isinstance(error, dict):
            code = error.get('properties', {}).get('code') if isinstance(error.get('properties'), dict) else None
            enum = code.get('enum') if isinstance(code, dict) else None
            if isinstance(enum, list) and all(isinstance(item, str) for item in enum):
                enums.append(set(enum))
        for value in document.values():
            enums.extend(_common_error_enums(value))
    elif isinstance(document, list):
        for item in document:
            enums.extend(_common_error_enums(item))
    return enums


def test_service_error_status_schema_and_openapi_enums_do_not_drift() -> None:
    from ai_model_serving.errors import ERROR_STATUS

    service_error_codes = _service_error_codes_from_source()
    schema_codes = set(json.loads((ROOT / 'specs/schemas/common_error.schema.json').read_text(encoding='utf-8'))['properties']['error']['properties']['code']['enum'])
    errors_codes = set(ERROR_STATUS)

    assert not sorted(service_error_codes - errors_codes), (
        'ServiceError code(s) missing from ERROR_STATUS: '
        + ', '.join(sorted(service_error_codes - errors_codes))
    )
    assert errors_codes == schema_codes, (
        'ERROR_STATUS and common_error.schema.json code enum drift: '
        f'only in ERROR_STATUS={sorted(errors_codes - schema_codes)}, '
        f'only in schema={sorted(schema_codes - errors_codes)}'
    )

    for rel in ('specs/openapi.gateway.yaml', 'specs/openapi.risk-adapter.yaml'):
        openapi = yaml.safe_load((ROOT / rel).read_text(encoding='utf-8'))
        enums = _common_error_enums(openapi)
        assert enums, f'{rel} does not expose CommonErrorResponse code enum'
        drift = [
            enum for enum in enums
            if enum != schema_codes
        ]
        assert not drift, f'{rel} CommonErrorResponse enum drift from common_error.schema.json'


def test_model_catalog_and_model_contracts_are_cross_checked() -> None:
    catalog = yaml.safe_load((ROOT / 'configs/model_catalog.yaml').read_text(encoding='utf-8'))['models']
    contracts = yaml.safe_load((ROOT / 'contracts/model_contracts.yaml').read_text(encoding='utf-8'))['models']
    assert set(contracts) == set(catalog) == {'local-main', 'local-embed', 'local-embed-ko', 'risk-prompt'}
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
    assert 'huggingface_hub==1.13.0' in runtime_lock_text
    for hf_transitive_pin in [
        'fsspec==2026.4.0',
        'hf-xet==1.4.3',
        'typer==0.23.1',
        'rich==15.0.0',
        'markdown-it-py==4.2.0',
        'mdurl==0.1.2',
        'shellingham==1.5.4',
    ]:
        assert hf_transitive_pin in runtime_lock_text
    assert 'pytest==' not in runtime_lock_text
    assert 'jsonschema==4.26.0' in runtime_lock_text
    assert 'torch==' not in runtime_lock_text
    assert 'vllm==' not in runtime_lock_text
    assert 'transformers==' not in runtime_lock_text
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
    assert features['tool_calling']['reasoning_parser'] == 'gemma4'
    assert features['tool_calling']['chat_template'] == '/app/configs/gemma4_chat_template.jinja'
    assert features['structured_outputs']['enabled'] is True
    assert features['structured_outputs']['backend'] == 'xgrammar'
    assert features['structured_outputs']['disable_any_whitespace'] is True
    assert features['structured_outputs']['enable_in_reasoning'] is True
    policy = main['request_parameter_policy']
    assert policy['allow_unlisted_parameters'] is False
    for field in ['top_p', 'top_k', 'min_p', 'repetition_penalty', 'n', 'tools', 'tool_choice', 'reasoning', 'response_format', 'logprobs', 'top_logprobs', 'logit_bias']:
        assert field in policy['supported_parameters']
    assert policy['reasoning']['enabled'] is True
    assert policy['reasoning']['default'] is False
    assert policy['response_format']['types'] == ['text', 'json_object', 'json_schema']
    assert policy['response_format']['json_object']['require_json_instruction'] is True
    assert policy['response_format']['json_schema']['max_schema_bytes'] == 16384
    assert policy['response_format']['json_schema']['require_additional_properties_false'] is True
    assert {
        '$dynamicRef',
        '$recursiveRef',
        '$dynamicAnchor',
        '$recursiveAnchor',
        '$id',
        '$anchor',
    }.issubset(set(policy['response_format']['json_schema']['disallowed_keywords']))
    assert policy['logprobs']['enabled'] is True
    assert policy['logprobs']['allow_stream'] is True
    assert policy['top_logprobs'] == {'min': 0, 'max': 10, 'requires_logprobs': True}
    assert policy['logit_bias']['token_id_semantics'] == 'served_model_tokenizer'
    assert policy['max_n'] == 1

    for key in ['risk_prompt']:
        assert serving[key]['runtime_features']['tool_calling']['enabled'] is False
        assert serving[key]['runtime_features']['prefix_caching']['enabled'] is False


def _compose_command_args(service_name: str) -> dict[str, str | bool]:
    compose = yaml.safe_load((ROOT / 'ops/compose/full-stack.private-network.yaml').read_text(encoding='utf-8'))
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


def test_main_runtime_compose_has_20k_o3_runtime_policy() -> None:
    serving = yaml.safe_load((ROOT / 'configs/model_serving.yaml').read_text(encoding='utf-8'))['models']['main_llm']
    catalog = yaml.safe_load((ROOT / 'configs/model_catalog.yaml').read_text(encoding='utf-8'))['models']['local-main']
    card = json.loads((ROOT / 'model_cards/local-main.json').read_text(encoding='utf-8'))
    args = _compose_command_args('main-llm-vllm')

    assert serving['max_model_len'] == 20000
    assert serving['max_num_batched_tokens'] == 20000
    assert serving['optimization_level'] == 3
    assert serving['gpu_memory_utilization'] == 0.76
    for source in [catalog['project_runtime_policy'], card['project_runtime_policy']]:
        assert source['max_model_len'] == serving['max_model_len']
        assert source['max_num_batched_tokens'] == serving['max_num_batched_tokens']
        assert source['optimization_level'] == serving['optimization_level']
        assert source['gpu_memory_utilization'] == serving['gpu_memory_utilization']
    assert int(args['max_model_len']) == serving['max_model_len']
    assert int(args['max_num_batched_tokens']) == serving['max_num_batched_tokens']
    assert int(args['optimization_level']) == serving['optimization_level']
    assert float(args['gpu_memory_utilization']) == serving['gpu_memory_utilization']


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
