from __future__ import annotations

from .common import (
    ROOT,
    read_yaml,
    service_default_host_ports,
)

def validate_ports() -> None:
    ports = service_default_host_ports()
    model_serving = read_yaml('configs/model_serving.yaml')
    checks = {}
    for key, cfg in model_serving['models'].items():
        if cfg.get('enabled', True) is True:
            checks[f'{key}_vllm'] = cfg['port']
    for key, value in checks.items():
        if ports.get(key) != value:
            raise SystemExit(f'port mismatch: {key} expected {value}, got {ports.get(key)}')

    gateway_port = ports['gateway']
    env = (ROOT / '.env.example').read_text(encoding='utf-8')
    if f'GATEWAY_PORT={gateway_port}' not in env:
        raise SystemExit(f'.env.example must include GATEWAY_PORT={gateway_port}')

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
        service_key = detector['service_key']
        catalog_tokens = catalog[logical_id]['runtime']['max_output_tokens']
        runtime_tokens = serving[service_key]['max_output_tokens']
        fixed_tokens = detector.get('fixed_parameters', {}).get('max_tokens')
        if not (catalog_tokens == runtime_tokens == fixed_tokens == 1):
            raise SystemExit(
                f'{logical_id} max_output_tokens must align at 1 across model catalog, runtime, and detector policy'
            )
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

def validate_model_resource_control_policy() -> None:
    from ai_model_serving.domain import ModelRegistry
    from ai_model_serving.main_model.control import load_main_model_catalog
    from ai_model_serving.registry_projection_drift import gpu_budget_status

    serving = read_yaml('configs/model_serving.yaml')
    catalog_document = read_yaml('configs/model_catalog.yaml')
    for key, cfg in serving['models'].items():
        if cfg.get('enabled', True) is not True:
            continue
        control = cfg.get('resource_control')
        if not isinstance(control, dict):
            raise SystemExit(f'{key} missing resource_control')
        required_fields = ['isolation', 'admission_control']
        if key != 'main_llm':
            required_fields.append('request_limits')
        for field in required_fields:
            if field not in control:
                raise SystemExit(f'{key} resource_control missing {field}')
        admission = control['admission_control']
        if key != 'main_llm' and admission.get('max_concurrency') != int(cfg.get('max_num_seqs', admission.get('max_concurrency'))):
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
    catalog = load_main_model_catalog(ROOT / 'configs' / 'main_model_profiles.yaml')
    for profile in catalog.profiles.values():
        if not profile.gateway_policy:
            raise SystemExit(f'{profile.profile_id} must declare gateway_policy')
        limits = profile.gateway_policy['request_limits']
        if 'image' in profile.capabilities['deployed_input']:
            if int(limits.get('max_image_inputs', 0)) != 1 or limits.get('allowed_image_url_schemes') != ['data']:
                raise SystemExit(f'{profile.profile_id} image input policy must allow exactly one data:image input')
            if int(limits.get('max_image_bytes', 0)) <= 0 or int(limits.get('max_image_pixels', 0)) <= 0:
                raise SystemExit(f'{profile.profile_id} image input policy must define decoded byte and pixel limits')
            if not limits.get('allowed_image_mime_types'):
                raise SystemExit(f'{profile.profile_id} image input policy must define allowed image MIME types')
