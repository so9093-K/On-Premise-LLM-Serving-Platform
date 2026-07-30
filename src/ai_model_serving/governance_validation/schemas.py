from __future__ import annotations

import re
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
    ROOT,
    read_json,
    read_yaml,
)

def resolve_ref(ref: str) -> Path:
    if not ref.startswith('./'):
        raise SystemExit(f'only local ./ refs are allowed in this package: {ref}')
    p = ROOT / 'specs' / ref[2:]
    if not p.exists():
        raise SystemExit(f'OpenAPI $ref target missing: {ref} -> {p}')
    return p

def walk_refs(obj: Any):
    if isinstance(obj, dict):
        if '$ref' in obj:
            yield obj['$ref']
        for v in obj.values():
            yield from walk_refs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_refs(v)

def walk_objects(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk_objects(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_objects(v)

def _data_url_pattern_suffixes(schema: dict[str, Any], media_kind: str) -> set[str]:
    patterns = [
        value
        for obj in walk_objects(schema)
        if isinstance((value := obj.get('pattern')), str) and value.startswith(f'^data:{media_kind}/')
    ]
    if len(patterns) != 1:
        raise SystemExit(f'chat completion schema must define exactly one data:{media_kind} pattern, found {patterns}')
    match = re.fullmatch(rf'\^data:{media_kind}/\(([^)]+)\);base64,', patterns[0])
    if not match:
        raise SystemExit(f'chat completion schema has an unexpected data:{media_kind} pattern: {patterns[0]}')
    return set(match.group(1).split('|'))

def _validate_chat_schema_media_policy(chat_schema: dict[str, Any]) -> None:
    limits = read_yaml('configs/model_serving.yaml')['models']['main_llm']['resource_control']['request_limits']
    expected_image_suffixes = {str(item).removeprefix('image/') for item in limits.get('allowed_image_mime_types', [])}
    expected_video_suffixes = {str(item).removeprefix('video/') for item in limits.get('allowed_video_mime_types', [])}
    expected_audio_formats = set(str(item) for item in limits.get('allowed_audio_formats', []))

    if _data_url_pattern_suffixes(chat_schema, 'image') != expected_image_suffixes:
        raise SystemExit('chat completion image data URL pattern must match configured allowed_image_mime_types')
    if _data_url_pattern_suffixes(chat_schema, 'video') != expected_video_suffixes:
        raise SystemExit('chat completion video data URL pattern must match configured allowed_video_mime_types')

    format_enums = [
        set(value)
        for obj in walk_objects(chat_schema)
        if isinstance((value := obj.get('enum')), list) and all(isinstance(item, str) for item in value)
    ]
    if expected_audio_formats not in format_enums:
        raise SystemExit('chat completion input_audio.format enum must match configured allowed_audio_formats')

def validate_openapi_refs() -> None:
    for path in ['specs/openapi.gateway.yaml', 'specs/openapi.risk-adapter.yaml']:
        doc = read_yaml(path)
        for ref in walk_refs(doc):
            resolve_ref(ref)

def validate_risk_schema() -> None:
    schema = read_json('specs/schemas/risk_assessment_response.schema.json')
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    valid_sample = {
        'assessment_id': 'risk_123',
        'status': 'completed',
        'risk_detected': True,
        'attention_required': True,
        'model_risk_detected': True,
        'system_signal_detected': False,
        'assessment_complete': True,
        'strongest_code': 'A1',
        'message': 'Risk signal detected.',
        'categories': [
            {'code': 'A1', 'family': 'prompt_attack', 'detected': True, 'confidence': None, 'source_model': 'risk-prompt', 'label': '<UNSAFE-A1>'}
        ],
        'system_signals': []
    }
    validator.validate(valid_sample)
    for field in FORBIDDEN_RESPONSE_FIELDS:
        invalid = dict(valid_sample)
        invalid[field] = True
        if not list(validator.iter_errors(invalid)):
            raise SystemExit(f'forbidden field was accepted by risk schema: {field}')

    semantic_invalid_samples = {
        'completed_requires_assessment_complete_true': {**valid_sample, 'assessment_complete': False},
        'a_code_requires_prompt_attack_family': {**valid_sample, 'categories': [{**valid_sample['categories'][0], 'family': 'policy_risk'}]},
        'risk_detected_requires_detected_category': {**valid_sample, 'categories': [{**valid_sample['categories'][0], 'detected': False}]},
        'system_signal_detected_requires_detected_signal': {**valid_sample, 'risk_detected': False, 'model_risk_detected': False, 'categories': [], 'system_signal_detected': True, 'strongest_code': 'INFERENCE_TIMEOUT', 'system_signals': [{'code': 'INFERENCE_TIMEOUT', 'detected': False, 'retryable': True}]},
    }
    for name, invalid in semantic_invalid_samples.items():
        if not list(validator.iter_errors(invalid)):
            raise SystemExit(f'risk schema semantic invariant failed to reject sample: {name}')

    partial_sample = {**valid_sample, 'status': 'partial', 'assessment_complete': False, 'risk_detected': False, 'model_risk_detected': False, 'system_signal_detected': True, 'strongest_code': 'INFERENCE_TIMEOUT', 'categories': [], 'system_signals': [{'code': 'INFERENCE_TIMEOUT', 'detected': True, 'retryable': True}]}
    validator.validate(partial_sample)

    configured = set(read_yaml('configs/model_serving.yaml')['risk_adapter']['forbidden_response_fields'])
    if configured != FORBIDDEN_RESPONSE_FIELDS:
        raise SystemExit(f'forbidden field list mismatch: config={configured}, expected={FORBIDDEN_RESPONSE_FIELDS}')

def validate_request_schemas() -> None:
    samples = {
        'specs/schemas/risk_assessment_request.schema.json': {'prompt': 'hello'},
        'specs/schemas/chat_completion_request.schema.json': {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'hello'}]},
        'specs/schemas/embedding_request.schema.json': {'model': 'local-embed', 'input': ['hello'], 'dimensions': 768},
        'specs/schemas/common_error.schema.json': {'error': {'code': 'VALIDATION_ERROR', 'message': 'msg', 'retryable': False, 'request_id': 'req_1'}},
        'specs/schemas/chat_completion_response.schema.json': {'id': 'chatcmpl_1', 'object': 'chat.completion', 'created': 1, 'model': 'local-main', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'hello'}, 'finish_reason': 'stop'}]},
        'specs/schemas/embedding_response.schema.json': {'object': 'list', 'model': 'local-embed', 'data': [{'object': 'embedding', 'embedding': [0.1, 0.2], 'index': 0}]},
        'specs/schemas/readiness_response.schema.json': {'status': 'ready', 'service': 'gateway', 'phase': 'serving', 'not_ready_dependencies': [], 'required_not_ready_dependencies': [], 'optional_not_ready_dependencies': [], 'dependencies': [{'name': 'main_llm_vllm', 'status': 'ready', 'endpoint': 'http://main-llm-vllm:9401/v1/models'}]},
        'specs/schemas/model_list_response.schema.json': {'object': 'list', 'data': [
            {'id': 'local-main', 'object': 'model', 'backend': 'main_llm_vllm', 'capabilities': ['chat.completions'], 'request_parameters': {'temperature': {'type': 'number', 'min': 0, 'max': 2}}},
            {'id': 'local-embed', 'object': 'model', 'backend': 'embedding_vllm', 'capabilities': ['embeddings'], 'request_parameters': {'dimensions': {'type': 'integer', 'enum': [768, 512, 256, 128]}}},
            {'id': 'local-embed-ko', 'object': 'model', 'backend': 'embedding_ko_vllm', 'capabilities': ['embeddings', 'retrieval_rerank'], 'request_parameters': {}},
            {'id': 'risk-prompt', 'object': 'model', 'backend': 'risk_adapter', 'capabilities': ['risk.prompt_attack_signal'], 'request_parameters': {}, 'fixed_parameters': {'max_tokens': 1, 'temperature': 0}},
        ]},
    }
    for path, sample in samples.items():
        schema = read_json(path)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(sample)

    chat_schema = read_json('specs/schemas/chat_completion_request.schema.json')
    _validate_chat_schema_media_policy(chat_schema)
    chat_validator = Draft202012Validator(chat_schema)
    accepted_chat_stream_sample = {'model': 'local-main', 'stream': True, 'stream_options': {'include_usage': True}, 'messages': [{'role': 'user', 'content': 'hello'}]}
    stream_errors = list(chat_validator.iter_errors(accepted_chat_stream_sample))
    if stream_errors:
        details = '; '.join(f'{".".join(str(p) for p in e.path)}: {e.message}' for e in stream_errors)
        raise SystemExit(
            f'specs/schemas/chat_completion_request.schema.json rejects a supported '
            f'stream=true+stream_options.include_usage sample: {details}'
        )
    accepted_advanced_chat_samples = [
        {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'Return JSON.'}], 'response_format': {'type': 'json_object'}},
        {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'Return JSON.'}], 'response_format': {'type': 'json_schema', 'json_schema': {'name': 'answer', 'strict': True, 'schema': {'type': 'object', 'additionalProperties': False, 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}}}},
        {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'hello'}], 'logprobs': True, 'top_logprobs': 10},
        {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'hello'}], 'logit_bias': {'42': -1.5}},
    ]
    for sample in accepted_advanced_chat_samples:
        if list(chat_validator.iter_errors(sample)):
            raise SystemExit(f'chat completion schema rejected supported advanced sample: {sample}')

    rejected_chat_samples = [
        {'model': 'local-main', 'stream': 'true', 'messages': [{'role': 'user', 'content': 'hello'}]},
        {'model': 'local-main', 'stream': True, 'stream_options': {'include_usage': 'yes'}, 'messages': [{'role': 'user', 'content': 'hello'}]},
        {'model': 'local-main', 'stream_options': {'include_usage': True}, 'messages': [{'role': 'user', 'content': 'hello'}]},
        {'model': 'local-main', 'stream': False, 'stream_options': {'include_usage': True}, 'messages': [{'role': 'user', 'content': 'hello'}]},
        {'model': 'local-main', 'tools': [], 'messages': [{'role': 'user', 'content': 'hello'}]},
        {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'hello', 'tool_calls': []}]},
        {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'hello'}], 'response_format': {'type': 'xml'}},
        {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'hello'}], 'response_format': {'type': 'text', 'json_schema': {'name': 'x', 'schema': {}}}},
        {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'hello'}], 'top_logprobs': 0},
        {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'hello'}], 'logprobs': True, 'top_logprobs': 11},
        {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'hello'}], 'logit_bias': {'x': 1}},
        {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'hello'}], 'logit_bias': {'1': True}},
    ]
    for sample in rejected_chat_samples:
        if not list(chat_validator.iter_errors(sample)):
            raise SystemExit(f'chat completion schema accepted unsupported sample: {sample}')


def validate_common_error_codes() -> None:
    schema = read_json('specs/schemas/common_error.schema.json')
    codes = set(schema['properties']['error']['properties']['code'].get('enum', []))
    required = {
        'VALIDATION_ERROR',
        'UNAUTHORIZED',
        'MODEL_UNAVAILABLE',
        'UPSTREAM_TIMEOUT',
        'UPSTREAM_SCHEMA_ERROR',
        'RUNTIME_NOT_READY',
        'INTERNAL_ERROR',
    }
    if not required.issubset(codes):
        raise SystemExit(f'common error code enum missing: {required - codes}')

def validate_openapi_error_surface() -> None:
    required_post_errors = {'413', '429', '500', '502', '503', '504'}
    for rel in ['specs/openapi.gateway.yaml', 'specs/openapi.risk-adapter.yaml']:
        doc = read_yaml(rel)
        for path, methods in doc.get('paths', {}).items():
            post = methods.get('post') if isinstance(methods, dict) else None
            if not post:
                continue
            responses = set(post.get('responses', {}))
            missing = required_post_errors - responses
            if missing:
                raise SystemExit(f'{rel} {path} missing error responses: {sorted(missing)}')
