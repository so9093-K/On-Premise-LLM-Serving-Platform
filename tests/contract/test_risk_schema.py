from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def load_schema(name: str) -> dict:
    return json.loads((ROOT / 'specs' / 'schemas' / name).read_text(encoding='utf-8'))


def valid_completed_sample() -> dict:
    return {
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
            {
                'code': 'A1',
                'family': 'prompt_attack',
                'detected': True,
                'confidence': None,
                'source_model': 'risk-prompt',
                'label': '<UNSAFE-A1>',
            }
        ],
        'system_signals': [],
    }


def test_valid_completed_risk_sample_passes() -> None:
    validator = Draft202012Validator(load_schema('risk_assessment_response.schema.json'))
    validator.validate(valid_completed_sample())


def test_usage_is_optional_but_must_be_complete_when_present() -> None:
    validator = Draft202012Validator(load_schema('risk_assessment_response.schema.json'))
    sample = valid_completed_sample() | {
        'usage': {'prompt_tokens': 139, 'completion_tokens': 1, 'total_tokens': 140}
    }
    validator.validate(sample)
    incomplete = valid_completed_sample() | {'usage': {'prompt_tokens': 139}}
    assert list(validator.iter_errors(incomplete))


def test_policy_decision_fields_are_forbidden() -> None:
    validator = Draft202012Validator(load_schema('risk_assessment_response.schema.json'))
    forbidden = {
        'allow',
        'review',
        'block',
        'decision',
        'action',
        'safe_to_send',
        'final_decision',
        'final_decision_owner',
        'policy_overrides',
    }
    for field in forbidden:
        sample = valid_completed_sample() | {field: True}
        assert list(validator.iter_errors(sample)), field


def test_category_code_family_invariant() -> None:
    validator = Draft202012Validator(load_schema('risk_assessment_response.schema.json'))
    sample = valid_completed_sample()
    sample['categories'][0]['family'] = 'policy_risk'
    assert list(validator.iter_errors(sample))


def test_mock_is_test_fixture_only() -> None:
    # Mock samples are allowed in test code, but production docs/scripts should only mention
    # the policy that mock behavior is test-only.
    assert 'tests/contract' in __file__.replace('\\\\', '/')
