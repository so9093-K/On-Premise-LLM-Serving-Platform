from __future__ import annotations

from pathlib import Path

from ai_model_serving.governance_validation import cli
from ai_model_serving.governance_validation.common import ROOT


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
