from __future__ import annotations

from pathlib import Path

from ai_model_serving.governance_validation import cli
from ai_model_serving.governance_validation.common import ROOT
from ai_model_serving.governance_validation.model_config import _without_reason_fields


def test_validate_contracts_script_is_thin_facade() -> None:
    script = ROOT / 'scripts/validation/validate_contracts.py'
    text = script.read_text(encoding='utf-8')

    assert 'ai_model_serving.governance_validation.cli import main' in text
    assert len(text.splitlines()) <= 30


def test_governance_validation_is_split_by_concern() -> None:
    package = ROOT / 'src/ai_model_serving/governance_validation'
    expected_modules = {
        'common.py',
        'filesystem.py',
        'versioning.py',
        'schemas.py',
        'model_config.py',
        'docs_ops.py',
        'release_runtime.py',
        'cli.py',
    }

    present = {path.name for path in package.glob('*.py')}
    assert expected_modules <= present
    assert all(len((package / module).read_text(encoding='utf-8').splitlines()) < 620 for module in expected_modules)


def test_governance_cli_exposes_main() -> None:
    assert callable(cli.main)


def test_without_reason_fields_strips_only_reason_suffixed_keys() -> None:
    # policy_reason/image_url_policy_reason are free-text rationale duplicated between
    # configs/model_catalog.yaml and model_cards/*.json; validate_model_cards() must not
    # fail model card equality checks over wording/translation edits to these fields,
    # only over actual behavioral/numeric drift (max_model_len, max_num_seqs, ...).
    policy = {
        "max_model_len": 20000,
        "policy_reason": "영어 문구",
        "image_url_policy_reason": "다른 문구",
    }
    assert _without_reason_fields(policy) == {"max_model_len": 20000}

    other_wording = {
        "max_model_len": 20000,
        "policy_reason": "completely different wording",
        "image_url_policy_reason": "completely different wording too",
    }
    assert _without_reason_fields(policy) == _without_reason_fields(other_wording)

    behavior_drift = {"max_model_len": 50000, "policy_reason": "영어 문구", "image_url_policy_reason": "다른 문구"}
    assert _without_reason_fields(policy) != _without_reason_fields(behavior_drift)
