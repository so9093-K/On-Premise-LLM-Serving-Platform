from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_command_terminology_policy_is_shared_and_enforced() -> None:
    policy_doc = (ROOT / 'docs/governance/policies/command_terminology_policy.md').read_text(encoding='utf-8')
    policy = yaml.safe_load((ROOT / 'configs/command_terminology_policy.yaml').read_text(encoding='utf-8'))
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')

    assert policy['policy_name'] == 'command_terminology_policy'
    assert policy['principle'] == 'standard_command_semantics'
    assert policy['canonical_commands']['build']['starts_services'] is False
    assert policy['canonical_commands']['start']['starts_services'] is True
    assert policy['canonical_commands']['ready']['starts_services'] is False

    for command, spec in policy['canonical_commands'].items():
        if spec.get('make_target_required'):
            assert f'{command}:' in makefile
    for alias, spec in policy['aliases'].items():
        if spec.get('make_target_required'):
            assert f'{alias}:' in makefile
            assert spec['canonical'] in policy['canonical_commands']

    for phrase in policy['required_documentation_phrases']:
        assert phrase in policy_doc

    terminology = (ROOT / 'docs/governance/terminology.md').read_text(encoding='utf-8')
    for term in ['build', 'start', 'up', 'down', 'ready', 'smoke', 'package', 'release', 'deploy']:
        assert term in terminology

