from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import (
    ROOT,
    read_json,
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

def _without_reason_fields(policy: dict[str, Any]) -> dict[str, Any]:
    # *_reason 필드(policy_reason, image_url_policy_reason, ...)는 동작이 아니라
    # 자유 텍스트 설명이다. model_catalog.yaml과 model_cards/*.json 사이에서
    # byte 단위로 동일할 것을 요구하면 실제 런타임 동작을 전혀 검증하지 못한 채
    # 무해한 문구/번역 수정만으로 CI가 깨진다; cross-file 일치가 필요한 것은
    # 구조적/수치 필드뿐이다.
    return {key: value for key, value in policy.items() if not key.endswith('_reason')}


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
        card_policy = _without_reason_fields(card.get('project_runtime_policy') or {})
        projection_policy = _without_reason_fields(projection.project_runtime_policy)
        if card_policy != projection_policy:
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
    # max_model_len 자체는 없을 수 있다(예: gemma4-12b-unified-fp8의 RedHatAI 카드는
    # $MAX_MODEL_LEN 플레이스홀더만 주고 구체적 예시값을 안 준다) -- 그 경우에도
    # 왜 없는지 note로 명시했는지는 강제해서, 값을 깜빡 빠뜨린 것과 의도적으로
    # 없는 것을 구분한다.
    if not source.get('tensor_parallel_size') or not (source.get('max_model_len') or source.get('note')):
        raise SystemExit(
            'local-main source_facts.upstream_example must preserve tensor_parallel_size, and '
            'either max_model_len or a note explaining why the upstream card has no concrete example'
        )
    if policy['tensor_parallel_size'] != serving['main_llm']['tensor_parallel_size']:
        raise SystemExit(
            f"local-main tensor_parallel_size mismatch: configs/model_catalog.yaml "
            f'project_runtime_policy={policy["tensor_parallel_size"]!r} vs '
            f'configs/model_serving.yaml main_llm={serving["main_llm"]["tensor_parallel_size"]!r}'
        )
    if policy['max_model_len'] != serving['main_llm']['max_model_len']:
        raise SystemExit(
            f"local-main max_model_len mismatch: configs/model_catalog.yaml "
            f'project_runtime_policy={policy["max_model_len"]!r} vs '
            f'configs/model_serving.yaml main_llm={serving["main_llm"]["max_model_len"]!r}'
        )
    if policy['max_num_batched_tokens'] != serving['main_llm']['max_num_batched_tokens']:
        raise SystemExit(
            f"local-main max_num_batched_tokens mismatch: configs/model_catalog.yaml "
            f'project_runtime_policy={policy["max_num_batched_tokens"]!r} vs '
            f'configs/model_serving.yaml main_llm={serving["main_llm"]["max_num_batched_tokens"]!r}'
        )
    if policy.get('optimization_level') != serving['main_llm'].get('optimization_level'):
        raise SystemExit(
            f"local-main optimization_level mismatch: configs/model_catalog.yaml "
            f'project_runtime_policy={policy.get("optimization_level")!r} vs '
            f'configs/model_serving.yaml main_llm={serving["main_llm"].get("optimization_level")!r}'
        )
    if main['source_facts']['upstream_context_length_tokens'].get('project_runtime_cap') != policy['max_model_len']:
        raise SystemExit(
            f"configs/model_catalog.yaml local-main: source_facts.upstream_context_length_tokens."
            f'project_runtime_cap={main["source_facts"]["upstream_context_length_tokens"].get("project_runtime_cap")!r} '
            f'must match project_runtime_policy.max_model_len={policy["max_model_len"]!r}'
        )
    if 'max_output_tokens' in serving['main_llm']:
        raise SystemExit(
            'configs/model_serving.yaml main_llm must not redeclare max_output_tokens '
            '(settings.py falls back to configs/model_catalog.yaml project_runtime_policy.max_output_tokens)'
        )
    if serving['main_llm'].get('runtime_policy_source') is None:
        raise SystemExit(
            'configs/model_serving.yaml main_llm is missing runtime_policy_source '
            '(free-text field documenting why this policy was chosen, see other models for examples)'
        )

    embed = catalog['local-embed']
    facts = embed['source_facts']
    embed_dims = embed.get('embedding_dimensions', {})
    if facts['max_input_tokens'] != embed['max_input_tokens']:
        raise SystemExit('local-embed source_facts.max_input_tokens must match catalog max_input_tokens')
    if facts['default_embedding_dimension'] != embed_dims.get('default'):
        raise SystemExit('local-embed source_facts.default_embedding_dimension must match catalog embedding_dimensions.default')
    if sorted(facts['matryoshka_dimensions']) != sorted(embed_dims.get('matryoshka_supported', [])):
        raise SystemExit('local-embed source_facts.matryoshka_dimensions must match catalog embedding_dimensions.matryoshka_supported')
    if serving['embedding']['max_model_len'] != facts['max_input_tokens']:
        raise SystemExit('embedding serving max_model_len must match model-card max input tokens')

    embed_ko = catalog['local-embed-ko']
    ko_facts = embed_ko['source_facts']
    ko_policy = embed_ko['project_runtime_policy']
    ko_embed_dims = embed_ko.get('embedding_dimensions', {})
    if ko_facts['output_dimensions'] != ko_embed_dims.get('default'):
        raise SystemExit('local-embed-ko source_facts.output_dimensions must match catalog embedding_dimensions.default')
    if ko_facts['max_sequence_length'] != embed_ko.get('max_input_tokens'):
        raise SystemExit('local-embed-ko source_facts.max_sequence_length must match catalog max_input_tokens')
    if ko_policy['embedding_dimension_supported'] != [ko_facts['output_dimensions']]:
        raise SystemExit('local-embed-ko project_runtime_policy must keep fixed 1024 dimensions')
    if ko_policy.get('retrieval_default') is not True:
        raise SystemExit('local-embed-ko project_runtime_policy must mark it as retrieval_default')
    if serving['embedding_ko']['port'] != embed_ko['runtime']['port']:
        raise SystemExit('local-embed-ko serving port must match model runtime port')

    compatibility_floor = str(
        read_yaml('configs/recommended_images.yaml')['images']['vllm'][
            'compatibility_pins'
        ]['transformers_min']
    )
    detector_specs = read_yaml('configs/model_serving.yaml')['risk_adapter'].get('detectors', {})
    for detector in detector_specs.values():
        if detector.get('type', 'vllm') == 'local':
            continue
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
        if facts.get('transformers_min_version') != compatibility_floor:
            raise SystemExit(
                f'{logical_id} must require transformers_min_version '
                f'{compatibility_floor} for Kanana risk vLLM config compatibility'
            )
        if not (policy['max_output_tokens'] == serving[serving_key]['max_output_tokens'] == model['runtime']['max_output_tokens'] == 1):
            raise SystemExit(f'{logical_id} max_output_tokens must remain 1 across source-aware policy and serving config')

    for logical_id in sorted(catalog):
        rel = f'model_cards/{logical_id}.json'
        card = read_json(rel)
        if 'source_facts' not in card or 'project_runtime_policy' not in card:
            raise SystemExit(f'{rel} must record source_facts and project_runtime_policy')
        if (
            rel.startswith('model_cards/risk-')
            and card['source_facts'].get('transformers_min_version')
            != compatibility_floor
        ):
            raise SystemExit(
                f'{rel} must require transformers_min_version '
                f'{compatibility_floor}'
            )

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
        raise SystemExit(
            f'ModelRegistry.public_logical_ids()={sorted(registry.public_logical_ids())!r} does not match '
            f'configs/model_catalog.yaml models with gateway_listing.enabled=true '
            f'(or default true)={sorted(expected_public)!r}'
        )

def validate_risk_detector_generation_budget() -> None:
    serving = read_yaml('configs/model_serving.yaml')['models']
    catalog = read_yaml('configs/model_catalog.yaml')['models']
    detector_specs = read_yaml('configs/model_serving.yaml')['risk_adapter'].get('detectors', {})
    for detector in detector_specs.values():
        if detector.get('type', 'vllm') == 'local':
            continue
        logical_id = detector['source_model']
        serving_key = detector['service_key']
        card = read_json(f'model_cards/{logical_id}.json')
        catalog_tokens = catalog[logical_id]['runtime']['max_output_tokens']
        card_tokens = card['runtime']['max_output_tokens']
        if not (catalog_tokens == card_tokens == 1):
            raise SystemExit(f'{logical_id} max_output_tokens must align at 1 across catalog and model card')
    risk_adapter_cfg = read_yaml('configs/model_serving.yaml')['risk_adapter']
    input_policy = risk_adapter_cfg.get('input_policy', {})
    max_prompt_chars = int(input_policy.get('max_prompt_chars', 0))
    enabled_detector_keys = [
        detector['service_key']
        for detector in detector_specs.values()
        if detector.get('enabled', True) is True and detector.get('type', 'vllm') != 'local'
    ]
    min_detector_window = min(int(serving[key]['max_model_len']) for key in enabled_detector_keys)
    expected_upper_bound = (min_detector_window - 64) * 4
    if max_prompt_chars <= 0 or max_prompt_chars > expected_upper_bound:
        raise SystemExit(
            f'configs/model_serving.yaml risk_adapter.input_policy.max_prompt_chars={max_prompt_chars} '
            f'must be > 0 and <= {expected_upper_bound} '
            f'(= (min detector max_model_len {min_detector_window} - 64) * 4 chars/token)'
        )
    if input_policy.get('overflow_signal') != 'TRUNCATED_INPUT':
        raise SystemExit(
            f'configs/model_serving.yaml risk_adapter.input_policy.overflow_signal must be '
            f'"TRUNCATED_INPUT", got {input_policy.get("overflow_signal")!r}'
        )
    if input_policy.get('overflow_action') != 'return_system_signal_without_detector_call':
        raise SystemExit(
            f'configs/model_serving.yaml risk_adapter.input_policy.overflow_action must be '
            f'"return_system_signal_without_detector_call", got {input_policy.get("overflow_action")!r}'
        )

def validate_model_resource_control_policy() -> None:
    from ai_model_serving.domain import ModelRegistry
    from ai_model_serving.registry_projection_drift import gpu_budget_status

    serving = read_yaml('configs/model_serving.yaml')
    catalog_document = read_yaml('configs/model_catalog.yaml')
    catalog = catalog_document['models']
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
    registry = ModelRegistry(catalog_document, serving)
    budget = gpu_budget_status(registry, read_yaml('configs/gpu_budgets.yaml'))
    if budget['over_avoid_threshold']:
        raise SystemExit(
            f'total configured gpu_memory_utilization {budget["total_gpu_memory_utilization"]} '
            f'must stay below avoid_above {budget["avoid_above"]}'
        )
    main_policy = catalog['local-main']['project_runtime_policy']
    if int(main_policy.get('max_image_inputs', 0)) != 1 or main_policy.get('allowed_image_url_schemes') != ['data']:
        raise SystemExit('local-main image input policy must allow exactly one data:image input by default')
    if int(main_policy.get('max_image_bytes', 0)) <= 0 or int(main_policy.get('max_image_pixels', 0)) <= 0:
        raise SystemExit('local-main image input policy must define decoded byte and pixel limits')
    expected_image_mime_types = set(serving['models']['main_llm']['resource_control']['request_limits'].get('allowed_image_mime_types', []))
    if set(main_policy.get('allowed_image_mime_types', [])) != expected_image_mime_types:
        raise SystemExit(
            'local-main image input policy MIME limits must match '
            'configs/model_serving.yaml local-main request_limits.allowed_image_mime_types'
        )
