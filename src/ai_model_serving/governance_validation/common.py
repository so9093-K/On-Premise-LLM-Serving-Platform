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
    raise RuntimeError('could not locate project root from governance_validation package')


ROOT = find_project_root()

FORBIDDEN_RESPONSE_FIELDS = {
    'allow', 'review', 'block', 'decision', 'action', 'safe_to_send',
    'final_decision', 'final_decision_owner', 'policy_overrides',
}
EXCLUDED_SCAN_PARTS = {
    '.git', '.venv', 'venv', 'env', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', '.eggs', 'dist', 'build', 'node_modules',
    '.runtime', 'model_cache', 'models', 'logs', 'run', 'outputs', '.cache',
    '.other',
}


def is_excluded_project_path(path: Path) -> bool:
    try:
        rel_parts = set(path.relative_to(ROOT).parts)
    except ValueError:
        return True
    return bool(rel_parts & EXCLUDED_SCAN_PARTS)


def service_default_host_ports() -> dict[str, int]:
    services = read_yaml('configs/services.yaml')['services']
    return {
        str(service_name): int(service['default_host_port'])
        for service_name, service in services.items()
    }


def iter_project_files(pattern: str = '*'):
    for candidate in ROOT.rglob(pattern):
        if candidate.is_file() and not is_excluded_project_path(candidate):
            yield candidate


def read_json(path: str) -> Any:
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def read_yaml(path: str) -> Any:
    return yaml.safe_load((ROOT / path).read_text(encoding='utf-8'))


def read_runtime_contract_text() -> str:
    paths = [
        'src/ai_model_serving/validation.py',
        'src/ai_model_serving/contracts/chat.py',
        'src/ai_model_serving/contracts/chat_common.py',
        'src/ai_model_serving/contracts/chat_json_schema.py',
        'src/ai_model_serving/contracts/chat_response_format.py',
        'src/ai_model_serving/contracts/chat_tools.py',
        'src/ai_model_serving/contracts/chat_request.py',
        'src/ai_model_serving/contracts/chat_response.py',
        'src/ai_model_serving/contracts/embedding.py',
        'src/ai_model_serving/contracts/media.py',
        'src/ai_model_serving/contracts/risk.py',
    ]
    return "\n".join((ROOT / path).read_text(encoding='utf-8') for path in paths)
