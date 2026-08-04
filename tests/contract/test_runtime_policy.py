"""모니터링/버전/모델 정책/에러 코드 등 여러 config·spec 파일이 서로 어긋나지
않았는지 확인하는 교차 검증 모음. governance_validation(make validate)이 이미
다루는 사실은 대상에서 제외한다 -- 각 테스트 안 주석에 그 경계를 명시해뒀다.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
