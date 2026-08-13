from __future__ import annotations

import ast
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
    profiles = read_yaml('configs/main_model_profiles.yaml').get('profiles', {})
    limits = [
        profile.get('gateway_policy', {}).get('request_limits', {})
        for profile in profiles.values()
        if isinstance(profile, dict)
    ]
    expected_image_suffixes = {
        str(item).removeprefix('image/') for policy in limits for item in policy.get('allowed_image_mime_types', [])
    }
    expected_video_suffixes = {
        str(item).removeprefix('video/') for policy in limits for item in policy.get('allowed_video_mime_types', [])
    }
    expected_audio_formats = {
        str(item) for policy in limits for item in policy.get('allowed_audio_formats', [])
    }

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

    usage_sample = {**valid_sample, 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
    validator.validate(usage_sample)
    incomplete_usage = {**valid_sample, 'usage': {'prompt_tokens': 1}}
    if not list(validator.iter_errors(incomplete_usage)):
        raise SystemExit('risk schema must reject an incomplete usage object')

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

    # ServiceError의 첫 인자는 실제 API 응답 error.code가 된다. 구현 전체를
    # 문자열로 검사하는 것이 아니라, 이 공개 경계에 도달하는 literal만 수집해
    # 에러 카탈로그/상태 정의 밖의 코드를 배포 전에 막는다.
    emitted_codes: set[str] = set()
    for path in (ROOT / 'src' / 'ai_model_serving').rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function = node.func
            is_service_error = (
                isinstance(function, ast.Name) and function.id == 'ServiceError'
            ) or (
                isinstance(function, ast.Attribute) and function.attr == 'ServiceError'
            )
            first_argument = node.args[0]
            if is_service_error and isinstance(first_argument, ast.Constant) and isinstance(first_argument.value, str):
                emitted_codes.add(first_argument.value)

    from ai_model_serving.errors import ERROR_STATUS

    catalog_codes = set(read_yaml('configs/error_catalog.yaml')['errors'])
    known_codes = set(ERROR_STATUS)
    if codes != known_codes:
        raise SystemExit(
            'common error schema and ERROR_STATUS disagree: '
            f'schema_only={sorted(codes - known_codes)}, '
            f'status_only={sorted(known_codes - codes)}'
        )
    if catalog_codes != known_codes:
        raise SystemExit(
            'error catalog and ERROR_STATUS disagree: '
            f'catalog_only={sorted(catalog_codes - known_codes)}, '
            f'status_only={sorted(known_codes - catalog_codes)}'
        )
    unknown_emitted = emitted_codes - known_codes
    if unknown_emitted:
        raise SystemExit(
            'ServiceError emits code(s) absent from the public error catalog: '
            + ', '.join(sorted(unknown_emitted))
        )

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
