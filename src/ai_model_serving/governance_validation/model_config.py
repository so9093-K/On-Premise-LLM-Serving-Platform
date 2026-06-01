from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit('PyYAML is required: pip install pyyaml') from exc

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:
    raise SystemExit('jsonschema is required: pip install jsonschema') from exc

from .common import (
    FORBIDDEN_RESPONSE_FIELDS,
    REQUIRED_FILES,
    ROOT,
    iter_project_files,
    read_json,
    read_runtime_contract_text,
    read_yaml,
    service_default_host_ports,
)

def validate_ports() -> None:
    ports = service_default_host_ports()
    model_serving = read_yaml('configs/model_serving.yaml')
    checks = {
        'gateway': model_serving['server']['gateway']['port'],
        'risk_adapter': model_serving['server']['risk_adapter']['port'],
    }
    for key, cfg in model_serving['models'].items():
        if cfg.get('enabled', True) is True:
            checks[f'{key}_vllm'] = cfg['port']
    for key, value in checks.items():
        if ports.get(key) != value:
            raise SystemExit(f'port mismatch: {key} expected {value}, got {ports.get(key)}')

    gateway_port = model_serving['server']['gateway']['port']
    env = (ROOT / '.env.example').read_text(encoding='utf-8')
    if f'GATEWAY_PORT={gateway_port}' not in env:
        raise SystemExit(f'.env.example must include GATEWAY_PORT={gateway_port}')

def validate_model_cards() -> None:
    from ai_model_serving.domain import ModelRegistry

    registry = ModelRegistry(read_yaml('configs/model_catalog.yaml'), read_yaml('configs/model_serving.yaml'))
    expected_ids = set(registry.logical_ids())
    card_ids = set()
    for projection in registry.model_card_projections():
        path = ROOT / f'model_cards/{projection.logical_id}.json'
        if not path.exists():
            raise SystemExit(f'missing model card: {path.relative_to(ROOT)}')
        card = json.loads(path.read_text(encoding='utf-8'))
        card_ids.add(str(card.get('logical_id')))
        if card.get('logical_id') != projection.logical_id:
            raise SystemExit(f'model card logical_id mismatch: {path.relative_to(ROOT)}')
        if card.get('upstream_model_id') != projection.upstream_model_id:
            raise SystemExit(f'model card upstream_model_id mismatch: {projection.logical_id}')
        if card.get('source_facts') != projection.source_facts:
            raise SystemExit(f'model card source_facts mismatch: {projection.logical_id}')
        if card.get('project_runtime_policy') != projection.project_runtime_policy:
            raise SystemExit(f'model card project_runtime_policy mismatch: {projection.logical_id}')
        runtime = card.get('runtime', {})
        for field, expected_value in projection.runtime.items():
            if field == 'served_model_name':
                continue
            if runtime.get(field) != expected_value:
                raise SystemExit(f'model card runtime.{field} mismatch for {projection.logical_id}: expected {expected_value}, got {runtime.get(field)}')
    if expected_ids != card_ids:
        raise SystemExit(f'model catalog/card mismatch: catalog={expected_ids}, cards={card_ids}')

def validate_model_source_facts() -> None:
    catalog = read_yaml('configs/model_catalog.yaml')['models']
    serving = read_yaml('configs/model_serving.yaml')['models']

    main = catalog['local-main']
    source = main['source_facts']['upstream_example']
    policy = main['project_runtime_policy']
    if not source.get('tensor_parallel_size') or not source.get('max_model_len'):
        raise SystemExit('local-main source_facts must preserve upstream example tensor_parallel_size and max_model_len')
    if policy['tensor_parallel_size'] != serving['main_llm']['tensor_parallel_size']:
        raise SystemExit('local-main tensor_parallel_size mismatch between catalog policy and serving config')
    if policy['max_model_len'] != serving['main_llm']['max_model_len']:
        raise SystemExit('local-main max_model_len mismatch between catalog policy and serving config')
    if policy['max_output_tokens'] != serving['main_llm']['max_output_tokens']:
        raise SystemExit('local-main max_output_tokens mismatch between catalog policy and serving config')
    if serving['main_llm'].get('runtime_policy_source') is None:
        raise SystemExit('local-main serving config must document the source/policy distinction')

    embed = catalog['local-embed']
    facts = embed['source_facts']
    if facts['max_input_tokens'] != 2048 or facts['default_embedding_dimension'] != 768:
        raise SystemExit('local-embed source_facts must preserve 2048 token input and 768 default dimension')
    if set(facts['matryoshka_dimensions']) != {768, 512, 256, 128}:
        raise SystemExit('local-embed source_facts must preserve Matryoshka dimensions')
    if serving['embedding']['max_model_len'] != facts['max_input_tokens']:
        raise SystemExit('embedding serving max_model_len must match model-card max input tokens')

    embed_ko = catalog['local-embed-ko']
    ko_facts = embed_ko['source_facts']
    ko_policy = embed_ko['project_runtime_policy']
    if ko_facts['output_dimensions'] != 1024 or ko_facts['max_sequence_length'] != 8192:
        raise SystemExit('local-embed-ko source_facts must preserve 1024 dimensions and 8192 model-card context')
    if ko_policy['embedding_dimension_supported'] != [ko_facts['output_dimensions']]:
        raise SystemExit('local-embed-ko project_runtime_policy must keep fixed 1024 dimensions')
    if ko_policy.get('retrieval_default') is not True:
        raise SystemExit('local-embed-ko project_runtime_policy must mark it as retrieval_default')
    if serving['embedding_ko']['port'] != embed_ko['runtime']['port']:
        raise SystemExit('local-embed-ko serving port must match model runtime port')

    detector_specs = read_yaml('configs/model_serving.yaml')['risk_adapter'].get('detectors', {})
    for detector in detector_specs.values():
        serving_key = detector['service_key']
        logical_id = detector['source_model']
        expected_codes = set(detector['allowed_codes'])
        model = catalog[logical_id]
        facts = model['source_facts']
        policy = model['project_runtime_policy']
        if facts['single_label_token'] is not True or facts['model_card_max_new_tokens'] != 1:
            raise SystemExit(f'{logical_id} source_facts must define one-token classifier output')
        if set(facts['known_codes']) != expected_codes:
            raise SystemExit(f'{logical_id} source_facts code mismatch')
        if facts.get('transformers_min_version') != '4.52.4':
            raise SystemExit(f'{logical_id} must require transformers_min_version 4.52.4 for Kanana risk vLLM config compatibility')
        if not (policy['max_output_tokens'] == serving[serving_key]['max_output_tokens'] == model['runtime']['max_output_tokens'] == 1):
            raise SystemExit(f'{logical_id} max_output_tokens must remain 1 across source-aware policy and serving config')

    for logical_id in sorted(catalog):
        rel = f'model_cards/{logical_id}.json'
        card = read_json(rel)
        forbidden_key = 'validation' + '_status'
        if forbidden_key in card:
            raise SystemExit(f'{rel} must not use legacy validation status for model identity or runtime policy')
        if 'source_facts' not in card or 'project_runtime_policy' not in card:
            raise SystemExit(f'{rel} must record source_facts and project_runtime_policy')
        if rel.startswith('model_cards/risk-') and card['source_facts'].get('transformers_min_version') != '4.52.4':
            raise SystemExit(f'{rel} must require transformers_min_version 4.52.4')

    review = (ROOT / 'docs/models/model_runtime_source_review.md').read_text(encoding='utf-8')
    for phrase in ['source_facts', 'project_runtime_policy', 'max_tokens=1', 'single-label-token']:
        if phrase not in review:
            raise SystemExit(f'model runtime source review missing phrase: {phrase}')

def validate_model_list_schema_enums() -> None:
    from ai_model_serving.domain import ModelRegistry

    schema = read_json('specs/schemas/model_list_response.schema.json')
    registry = ModelRegistry(read_yaml('configs/model_catalog.yaml'), read_yaml('configs/model_serving.yaml'))
    projected_schema = registry.model_list_schema_document()
    if schema != projected_schema:
        raise SystemExit('model_list_response.schema.json must match ModelRegistry.model_list_schema_document()')

    gateway_source = (ROOT / 'src/ai_model_serving/apps/gateway.py').read_text(encoding='utf-8')
    settings_source = (ROOT / 'src/ai_model_serving/settings.py').read_text(encoding='utf-8')
    if 'MODEL_LIST' in gateway_source:
        raise SystemExit('gateway.py must not define a hardcoded MODEL_LIST')
    if '_public_models_from_registry' not in settings_source:
        raise SystemExit('settings.py must build Gateway model list through ModelRegistry')

def validate_model_registry_alignment() -> None:
    from ai_model_serving.domain import ModelRegistry

    registry = ModelRegistry(read_yaml('configs/model_catalog.yaml'), read_yaml('configs/model_serving.yaml'))
    issues = registry.alignment_issues()
    if issues:
        details = '; '.join(f'{issue.code}: {issue.message}' for issue in issues)
        raise SystemExit(f'ModelRegistry alignment failed: {details}')
    catalog = read_yaml('configs/model_catalog.yaml')['models']
    expected_public = {logical_id for logical_id, cfg in catalog.items() if cfg.get('gateway_listing', {}).get('enabled', True) is True}
    if set(registry.public_logical_ids()) != expected_public:
        raise SystemExit('ModelRegistry public logical ids must match enabled gateway listings')
    settings_text = (ROOT / 'src/ai_model_serving/settings.py').read_text(encoding='utf-8')
    if '_public_models_from_registry(model_catalog, model_serving)' not in settings_text:
        raise SystemExit('settings.py must build Gateway model list through ModelRegistry with serving alignment')

def validate_resource_requirements_doc() -> None:
    resource = (ROOT / 'docs/resources/gpu_resource_requirements_48gb.md').read_text(encoding='utf-8')
    for phrase in ['48GB VRAM 단일 GPU', 'gpu-memory-utilization', '0.83~0.87', 'runtime peak']:
        if phrase not in resource:
            raise SystemExit(f'GPU resource requirements doc missing phrase: {phrase}')
    reference = (ROOT / 'docs/resources/gpu_resource_plan.md').read_text(encoding='utf-8')
    if 'runtime' not in reference.lower() or 'runtime validation report' not in reference.lower():
        raise SystemExit('GPU resource reference must keep runtime validation report language')

def validate_risk_detector_generation_budget() -> None:
    serving = read_yaml('configs/model_serving.yaml')['models']
    catalog = read_yaml('configs/model_catalog.yaml')['models']
    detector_specs = read_yaml('configs/model_serving.yaml')['risk_adapter'].get('detectors', {})
    for detector in detector_specs.values():
        logical_id = detector['source_model']
        serving_key = detector['service_key']
        card = read_json(f'model_cards/{logical_id}.json')
        catalog_tokens = catalog[logical_id]['runtime']['max_output_tokens']
        serving_tokens = serving[serving_key]['max_output_tokens']
        card_tokens = card['runtime']['max_output_tokens']
        if not (catalog_tokens == serving_tokens == card_tokens == 1):
            raise SystemExit(f'{logical_id} max_output_tokens must align at 1 across catalog, serving, and model card')
    risk_adapter_cfg = read_yaml('configs/model_serving.yaml')['risk_adapter']
    input_policy = risk_adapter_cfg.get('input_policy', {})
    max_prompt_chars = int(input_policy.get('max_prompt_chars', 0))
    enabled_detector_keys = [
        detector['service_key']
        for detector in detector_specs.values()
        if detector.get('enabled', True) is True
    ]
    min_detector_window = min(int(serving[key]['max_model_len']) for key in enabled_detector_keys)
    expected_upper_bound = (min_detector_window - 64) * 4
    if max_prompt_chars <= 0 or max_prompt_chars > expected_upper_bound:
        raise SystemExit(
            'risk_adapter.input_policy.max_prompt_chars must be a positive detector-context guard ' 
            f'not exceeding {expected_upper_bound} chars'
        )
    if input_policy.get('overflow_signal') != 'TRUNCATED_INPUT':
        raise SystemExit('risk_adapter.input_policy.overflow_signal must be TRUNCATED_INPUT')
    if input_policy.get('overflow_action') != 'return_system_signal_without_detector_call':
        raise SystemExit('risk_adapter.input_policy.overflow_action must avoid detector calls for overflow input')

    risk_text = (ROOT / 'src/ai_model_serving/apps/risk_adapter.py').read_text(encoding='utf-8')
    risk_service_text = (ROOT / 'src/ai_model_serving/services/risk_assessment.py').read_text(encoding='utf-8')
    risk_input_text = (ROOT / 'src/ai_model_serving/risk_input.py').read_text(encoding='utf-8')
    if '"max_tokens": detector.max_output_tokens' not in risk_service_text:
        raise SystemExit('risk adapter detector calls must use configured single-token detector budget')
    if 'RiskInputPolicy' not in risk_service_text or 'TRUNCATED_INPUT' not in risk_input_text:
        raise SystemExit('risk adapter must guard detector context overflow with TRUNCATED_INPUT system signal')

def validate_model_contracts_cross_reference() -> None:
    from ai_model_serving.domain import ModelRegistry

    contracts = read_yaml('contracts/model_contracts.yaml')
    registry = ModelRegistry(read_yaml('configs/model_catalog.yaml'), read_yaml('configs/model_serving.yaml'))
    projected = registry.model_contracts_document()
    if contracts != projected:
        raise SystemExit(f'model_contracts.yaml must match ModelRegistry projection: expected={projected}, actual={contracts}')

def validate_smoke_thresholds() -> None:
    script = (ROOT / 'scripts/ops/smoke_test.sh').read_text(encoding='utf-8')
    for phrase in ['SMOKE_MAX_REQUEST_SECONDS', 'SMOKE_MAX_LATENCY_MS', '--max-time', 'check_latency']:
        if phrase not in script:
            raise SystemExit(f'smoke_test.sh missing threshold support: {phrase}')

def validate_model_resource_control_policy() -> None:
    serving = read_yaml('configs/model_serving.yaml')
    catalog = read_yaml('configs/model_catalog.yaml')['models']
    for key, cfg in serving['models'].items():
        if cfg.get('enabled', True) is not True:
            continue
        control = cfg.get('resource_control')
        if not isinstance(control, dict):
            raise SystemExit(f'{key} missing resource_control')
        for field in ['isolation', 'admission_control', 'request_limits', 'degrade_action']:
            if field not in control:
                raise SystemExit(f'{key} resource_control missing {field}')
        admission = control['admission_control']
        if admission.get('max_concurrency') != int(cfg.get('max_num_seqs', admission.get('max_concurrency'))):
            raise SystemExit(f'{key} resource control concurrency should track max_num_seqs')
        if float(cfg.get('gpu_memory_utilization', 0)) <= 0 or float(cfg.get('gpu_memory_utilization', 0)) >= 1:
            raise SystemExit(f'{key} gpu_memory_utilization must be between 0 and 1')
    total_util = sum(float(cfg['gpu_memory_utilization']) for cfg in serving['models'].values() if cfg.get('enabled', True) is True)
    gpu = read_yaml('configs/gpu_budgets.yaml')
    util_policy = gpu['gpu']['total_gpu_memory_utilization']
    avoid_above = float(util_policy['avoid_above'])
    recommended_start = float(util_policy['recommended_start'])
    if total_util >= avoid_above:
        raise SystemExit(f'total configured gpu_memory_utilization {total_util} must stay below avoid_above {avoid_above}')
    if round(total_util, 6) != round(recommended_start, 6):
        raise SystemExit(f'total configured gpu_memory_utilization {total_util} must match recommended_start {recommended_start}')
    if gpu['gpu'].get('default_profile') != 'single_a6000_conservative':
        raise SystemExit('gpu_budgets.yaml must define single_a6000_conservative as the default profile')
    fixed_constraints = gpu.get('resource_management', {}).get('fixed_constraints', [])
    for phrase in ['risk detector max_output_tokens remains 1', 'model fallback is not allowed', 'each model keeps an independent vLLM process and port']:
        if phrase not in fixed_constraints:
            raise SystemExit(f'gpu_budgets.yaml missing fixed constraint: {phrase}')
    allocation_doc = ROOT / 'docs/resources/gpu_resource_plan.md'
    if not allocation_doc.exists():
        raise SystemExit('docs/resources/gpu_resource_plan.md is required')
    allocation_text = allocation_doc.read_text(encoding='utf-8')
    expected_total_phrase = f'설정된 enabled `gpu_memory_utilization` 합계: `{recommended_start:g}`'
    for phrase in ['single_a6000_conservative', expected_total_phrase, 'Tuning order', 'Fixed constraints']:
        if phrase not in allocation_text:
            raise SystemExit(f'gpu_resource_plan.md missing phrase: {phrase}')
    main_policy = catalog['local-main']['project_runtime_policy']
    expected_max_output_tokens = int(gpu['limits']['main_llm_max_output_tokens'])
    if set(main_policy.get('input_modalities', [])) != {'text', 'image'}:
        raise SystemExit('local-main project runtime policy must define text+image input modalities')
    if int(main_policy.get('max_output_tokens', 0)) != expected_max_output_tokens:
        raise SystemExit(
            f'local-main max_output_tokens {main_policy.get("max_output_tokens")} must match '
            f'gpu_budgets.yaml main_llm_max_output_tokens {expected_max_output_tokens}'
        )
    if int(main_policy.get('max_image_inputs', 0)) != 1 or main_policy.get('allowed_image_url_schemes') != ['data']:
        raise SystemExit('local-main image input policy must allow exactly one data:image input by default')
    if int(main_policy.get('max_image_bytes', 0)) <= 0 or int(main_policy.get('max_image_pixels', 0)) <= 0:
        raise SystemExit('local-main image input policy must define decoded byte and pixel limits')
    if set(main_policy.get('allowed_image_mime_types', [])) != {'image/jpeg', 'image/png', 'image/webp'}:
        raise SystemExit('local-main image input policy must define MIME limits')

def validate_model_lifecycle_docs_and_cli() -> None:
    doc = (ROOT / 'docs/operations/model_runtime_control.md').read_text(encoding='utf-8')
    for phrase in ['Add a model', 'Remove a model', 'Model independence', 'Input and output contracts']:
        if phrase not in doc:
            raise SystemExit(f'model runtime control doc missing phrase: {phrase}')
    if 'model-inventory:' not in (ROOT / 'Makefile').read_text(encoding='utf-8'):
        raise SystemExit('Makefile must expose model-inventory target')
    inventory_script = (ROOT / 'scripts/models/model_inventory.py').read_text(encoding='utf-8')
    render_script = (ROOT / 'scripts/models/render_vllm_commands.py').read_text(encoding='utf-8')
    if 'ModelRegistry' not in inventory_script or 'inventory_rows()' not in inventory_script:
        raise SystemExit('scripts/models/model_inventory.py must use ModelRegistry inventory projections')
    if 'ModelRegistry' not in render_script or 'iter_runtime_services()' not in render_script:
        raise SystemExit('scripts/models/render_vllm_commands.py must use ModelRegistry runtime-service projections')
    if not os.access(ROOT / 'scripts/models/model_inventory.py', os.X_OK):
        raise SystemExit('scripts/models/model_inventory.py must be executable')
