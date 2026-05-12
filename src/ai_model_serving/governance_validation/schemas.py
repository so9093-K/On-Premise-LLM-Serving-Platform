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
    EXPECTED_PORTS,
    FORBIDDEN_RESPONSE_FIELDS,
    REQUIRED_FILES,
    ROOT,
    iter_project_files,
    read_json,
    read_runtime_contract_text,
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
        'specs/schemas/readiness_response.schema.json': {'status': 'ready', 'service': 'gateway', 'phase': 'serving', 'not_ready_dependencies': [], 'dependencies': [{'name': 'main_llm_vllm', 'status': 'ready', 'endpoint': 'http://main-llm-vllm:9401/v1/models'}]},
        'specs/schemas/model_list_response.schema.json': {'object': 'list', 'data': [
            {'id': 'local-main', 'object': 'model', 'backend': 'main_llm_vllm', 'capabilities': ['chat.completions'], 'request_parameters': {'temperature': {'type': 'number', 'min': 0, 'max': 2}}},
            {'id': 'local-embed', 'object': 'model', 'backend': 'embedding_vllm', 'capabilities': ['embeddings'], 'request_parameters': {'dimensions': {'type': 'integer', 'enum': [768, 512, 256, 128]}}},
            {'id': 'risk-prompt', 'object': 'model', 'backend': 'risk_adapter', 'capabilities': ['risk.prompt_attack_signal'], 'request_parameters': {}, 'fixed_parameters': {'max_tokens': 1, 'temperature': 0}},
        ]},
    }
    for path, sample in samples.items():
        schema = read_json(path)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(sample)

    chat_schema = read_json('specs/schemas/chat_completion_request.schema.json')
    chat_validator = Draft202012Validator(chat_schema)
    accepted_chat_stream_sample = {'model': 'local-main', 'stream': True, 'stream_options': {'include_usage': True}, 'messages': [{'role': 'user', 'content': 'hello'}]}
    if list(chat_validator.iter_errors(accepted_chat_stream_sample)):
        raise SystemExit('chat completion schema rejected supported stream=true sample')

    rejected_chat_samples = [
        {'model': 'local-main', 'stream': 'true', 'messages': [{'role': 'user', 'content': 'hello'}]},
        {'model': 'local-main', 'stream': True, 'stream_options': {'include_usage': 'yes'}, 'messages': [{'role': 'user', 'content': 'hello'}]},
        {'model': 'local-main', 'stream_options': {'include_usage': True}, 'messages': [{'role': 'user', 'content': 'hello'}]},
        {'model': 'local-main', 'stream': False, 'stream_options': {'include_usage': True}, 'messages': [{'role': 'user', 'content': 'hello'}]},
        {'model': 'local-main', 'tools': [], 'messages': [{'role': 'user', 'content': 'hello'}]},
        {'model': 'local-main', 'messages': [{'role': 'user', 'content': 'hello', 'tool_calls': []}]},
    ]
    for sample in rejected_chat_samples:
        if not list(chat_validator.iter_errors(sample)):
            raise SystemExit(f'chat completion schema accepted unsupported sample: {sample}')

    errors_py = (ROOT / 'src/ai_model_serving/errors.py').read_text(encoding='utf-8')
    error_codes = set(read_json('specs/schemas/common_error.schema.json')['properties']['error']['properties']['code']['enum'])
    for code in error_codes:
        if f'\"{code}\"' not in errors_py:
            raise SystemExit(f'common error schema code missing from errors.py ERROR_STATUS: {code}')


def validate_generated_openapi_contract_schemas() -> None:
    """Ensure FastAPI-generated OpenAPI surfaces the checked-in JSON contracts."""
    from ai_model_serving.apps.gateway import create_gateway_app
    from ai_model_serving.apps.risk_adapter import create_risk_adapter_app
    from ai_model_serving.openapi_contracts import load_contract_schema

    gateway = create_gateway_app().openapi()
    risk_adapter = create_risk_adapter_app().openapi()

    expected_gateway_requests = {
        '/v1/chat/completions': 'chat_completion_request.schema.json',
        '/v1/embeddings': 'embedding_request.schema.json',
        '/v1/risk/detectors/prompt/assessments': 'risk_assessment_request.schema.json',
        '/v1/risk/detectors/siren/assessments': 'risk_assessment_request.schema.json',
        '/v1/risk/assessments': 'risk_assessment_request.schema.json',
    }
    expected_gateway_responses = {
        '/v1/models': ('get', 'model_list_response.schema.json'),
        '/v1/chat/completions': ('post', 'chat_completion_response.schema.json'),
        '/v1/embeddings': ('post', 'embedding_response.schema.json'),
        '/v1/risk/detectors/prompt/assessments': ('post', 'risk_assessment_response.schema.json'),
        '/v1/risk/detectors/siren/assessments': ('post', 'risk_assessment_response.schema.json'),
        '/v1/risk/assessments': ('post', 'risk_assessment_response.schema.json'),
    }
    expected_risk_requests = {
        '/v1/risk/detectors/prompt/assessments': 'risk_assessment_request.schema.json',
        '/v1/risk/detectors/siren/assessments': 'risk_assessment_request.schema.json',
        '/v1/risk/assessments': 'risk_assessment_request.schema.json',
    }

    for path, schema_name in expected_gateway_requests.items():
        actual = gateway['paths'][path]['post']['requestBody']['content']['application/json']['schema']
        if actual != load_contract_schema(schema_name, root=ROOT):
            raise SystemExit(f'generated Gateway OpenAPI request schema drift: {path} != {schema_name}')
    for path, (method, schema_name) in expected_gateway_responses.items():
        actual = gateway['paths'][path][method]['responses']['200']['content']['application/json']['schema']
        if actual != load_contract_schema(schema_name, root=ROOT):
            raise SystemExit(f'generated Gateway OpenAPI response schema drift: {path} != {schema_name}')
    for path, schema_name in expected_risk_requests.items():
        actual = risk_adapter['paths'][path]['post']['requestBody']['content']['application/json']['schema']
        if actual != load_contract_schema(schema_name, root=ROOT):
            raise SystemExit(f'generated Risk Adapter OpenAPI request schema drift: {path} != {schema_name}')
        response = risk_adapter['paths'][path]['post']['responses']['200']['content']['application/json']['schema']
        if response != load_contract_schema('risk_assessment_response.schema.json', root=ROOT):
            raise SystemExit(f'generated Risk Adapter OpenAPI response schema drift: {path}')

    expected_post_error_codes = {'401', '413', '422', '429', '500', '502', '503', '504'}
    for name, doc in {'Gateway': gateway, 'Risk Adapter': risk_adapter}.items():
        for path, methods in doc.get('paths', {}).items():
            post = methods.get('post') if isinstance(methods, dict) else None
            if not post:
                continue
            statuses = set(post.get('responses', {}))
            missing = expected_post_error_codes - statuses
            if missing:
                raise SystemExit(f'generated {name} OpenAPI post error surface drift: {path} missing {sorted(missing)}')


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
