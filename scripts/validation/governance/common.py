from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit('PyYAML is required: pip install pyyaml') from exc


def find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / 'VERSION').exists() and (parent / 'configs').exists():
            return parent
    raise RuntimeError('could not locate project root from governance validation package')


ROOT = find_project_root()

FORBIDDEN_RESPONSE_FIELDS = {
    'allow', 'review', 'block', 'decision', 'action', 'safe_to_send',
    'final_decision', 'final_decision_owner', 'policy_overrides',
}


def service_default_host_ports() -> dict[str, int]:
    services = read_yaml('configs/services.yaml')['services']
    return {
        str(service_name): int(service['default_host_port'])
        for service_name, service in services.items()
    }


def read_json(path: str) -> Any:
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def read_yaml(path: str) -> Any:
    return yaml.safe_load((ROOT / path).read_text(encoding='utf-8'))
