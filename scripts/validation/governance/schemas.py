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
        # 이 sample은 readiness schema의 URI 필드 형태만 검증한다. 실제 서비스명·포트는
        # configs/services.yaml과 configs/model_serving.yaml의 정합성 검증이 소유한다.
        'specs/schemas/readiness_response.schema.json': {'status': 'ready', 'service': 'gateway', 'phase': 'serving', 'not_ready_dependencies': [], 'required_not_ready_dependencies': [], 'optional_not_ready_dependencies': [], 'dependencies': [{'name': 'main_llm_vllm', 'status': 'ready', 'endpoint': 'http://runtime.invalid:1/v1/models'}]},
    }
    for path, sample in samples.items():
        schema = read_json(path)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(sample)

    chat_schema = read_json('specs/schemas/chat_completion_request.schema.json')
    _validate_chat_schema_media_policy(chat_schema)
    _validate_chat_request_contract_samples(Draft202012Validator(chat_schema))


def _chat_request_profile_policies() -> list[tuple[str, dict[str, Any]]]:
    """배포 프로필이 선언한 request parameter 정책들을 모은다."""
    profiles = read_yaml('configs/main_model_profiles.yaml').get('profiles', {})
    policies = [
        (str(profile_id), policy)
        for profile_id, profile in profiles.items()
        if isinstance(profile, dict)
        and isinstance(policy := (profile.get('gateway_policy') or {}).get('request_parameter_policy'), dict)
    ]
    if not policies:
        raise SystemExit('configs/main_model_profiles.yaml에 request_parameter_policy를 가진 프로필이 없다')
    return policies


def _validate_chat_request_contract_samples(chat_validator: Draft202012Validator) -> None:
    """요청 계약의 두 구현이 같은 샘플에 대해 어긋나지 않는지 확인한다.

    chat/completions 요청 계약은 두 벌로 존재한다: 클라이언트가 보는 공개 JSON
    Schema와, Gateway가 런타임에 실제로 거는 validate_chat_request()다. 예전엔 각자
    자기 샘플 목록을 들고 따로 검사받았고, **둘이 서로 일치하는지는 아무도 보지
    않았다.** 그래서 여기서 한 corpus로 양쪽을 함께 돌린다.

    강제하는 방향은 하나다: **런타임이 받아들이는 요청을 공개 스키마가 거부하면 안 된다.**
    그건 스펙이 거짓말을 하는 것이고, 스펙대로 만든 클라이언트가 멀쩡한 요청을 못 보낸다.
    반대 방향(스키마는 받는데 특정 프로필이 거부)은 정상이다 -- 스키마는 배포 전체가
    지원할 수 있는 상한이고, 프로필별 정책은 그보다 좁을 수 있다.
    """
    import copy

    from ai_model_serving.contracts.chat_request import validate_chat_request
    from ai_model_serving.errors import ServiceError

    corpus = read_json('specs/chat_request_contract_samples.json')
    expected_model = corpus['expected_model']
    policies = _chat_request_profile_policies()

    for sample in corpus['samples']:
        name = sample['name']
        payload = sample['payload']
        expects_accept = sample['expect'] == 'accept'

        schema_accepts = not list(chat_validator.iter_errors(payload))
        if schema_accepts != expects_accept:
            verdict = 'accepted' if schema_accepts else 'rejected'
            raise SystemExit(
                f'chat_completion_request.schema.json {verdict} sample {name!r} '
                f'but the corpus expects {sample["expect"]}'
            )

        for profile_id, policy in policies:
            try:
                validate_chat_request(
                    copy.deepcopy(payload),
                    expected_model=expected_model,
                    request_parameter_policy=policy,
                )
                runtime_accepts = True
            except ServiceError:
                runtime_accepts = False

            if runtime_accepts and not schema_accepts:
                raise SystemExit(
                    f'런타임 검증기는 sample {name!r}을 프로필 {profile_id!r}에서 받아들이는데 '
                    'specs/schemas/chat_completion_request.schema.json은 거부한다 — '
                    '공개 스키마가 실제 API보다 좁다.'
                )


def validate_common_error_codes() -> None:
    """공개 error code 집합이 런타임·스키마·카탈로그 세 곳에서 같은지 확인한다.

    ERROR_STATUS(errors.py)가 코드 목록의 권위다. 나머지 둘은 그 목록에 딸린
    표현이다 -- 공개 스키마의 enum은 클라이언트가 보는 목록이고, error_catalog.yaml은
    code별 의미/조치 서술이다. 셋 중 하나만 추가·삭제되면 여기서 막는다.

    retryable은 여기서 보지 않는다. 예전엔 error_catalog.yaml이 ERROR_RETRYABLE의
    사본을 들고 있어서 이 함수가 둘의 일치를 지켜야 했는데, 사본을 없애고 문서 gloss도
    ERROR_RETRYABLE을 직접 읽게 바꿨다 -- 지킬 사본이 없으면 검사도 필요 없다.
    """
    from ai_model_serving.errors import ERROR_RETRYABLE, ERROR_STATUS

    known_codes = set(ERROR_STATUS)

    schema = read_json('specs/schemas/common_error.schema.json')
    schema_codes = set(schema['properties']['error']['properties']['code'].get('enum', []))
    if schema_codes != known_codes:
        raise SystemExit(
            'common_error.schema.json code enum이 errors.ERROR_STATUS와 다르다: '
            f'스키마에만={sorted(schema_codes - known_codes)}, '
            f'코드에만={sorted(known_codes - schema_codes)}'
        )

    catalog_codes = set(read_yaml('configs/error_catalog.yaml')['errors'])
    if catalog_codes != known_codes:
        raise SystemExit(
            'error_catalog.yaml이 errors.ERROR_STATUS와 다르다: '
            f'카탈로그에만={sorted(catalog_codes - known_codes)}, '
            f'코드에만={sorted(known_codes - catalog_codes)}'
        )

    missing_retryable = known_codes - set(ERROR_RETRYABLE)
    if missing_retryable:
        raise SystemExit(f'ERROR_RETRYABLE missing entries for: {sorted(missing_retryable)}')

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
