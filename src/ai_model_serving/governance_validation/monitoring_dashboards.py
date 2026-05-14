from __future__ import annotations

import json

from ai_model_serving.governance_validation.common import read_json


def validate_grafana_dashboard_templates() -> None:
    required_titles = {
        'ops/grafana/dashboards/executive_runtime_overview.json': {
            'Overall Status', 'User Traffic', 'p95 Latency (5m)', 'Error Rate',
            'GPU Headroom', 'Component Readiness', 'Scrape Health',
        },
        'ops/grafana/dashboards/gpu_capacity_and_oom_risk.json': {
            'GPU Headroom', 'GPU Memory Used', 'OOM or Restart Events',
            'GPU Utilization', 'VRAM Used vs Budget',
        },
        'ops/grafana/dashboards/risk_signal_operations.json': {
            'Risk Assessment Volume', 'Risk Detected Rate', 'Forbidden Field Violations',
        },
        'ops/grafana/dashboards/chat_api_deep_dive.json': {
            'Chat Request Rate', 'Streaming Errors', 'Usage Chunk Events',
        },
        'ops/grafana/dashboards/model_runtime_deep_dive.json': {
            'Token Throughput', 'Queue Depth', 'KV Cache Usage',
        },
    }
    expected_variables = {'datasource', 'window', 'model', 'runtime_service', 'route', 'status_code'}
    for path, titles in required_titles.items():
        dash = read_json(path)
        if not dash.get('uid') or 'contract-reference' not in dash.get('tags', []):
            raise SystemExit(f'grafana dashboard template missing reference metadata: {path}')
        variables = {item.get('name') for item in dash.get('templating', {}).get('list', [])}
        if not expected_variables.issubset(variables):
            raise SystemExit(f'grafana dashboard missing variables {expected_variables - variables}: {path}')
        if ' / ' in str(dash.get('title', '')):
            raise SystemExit(f'grafana dashboard title must be English-only: {path}')
        if not dash.get('panels') or dash['panels'][0].get('type') != 'text':
            raise SystemExit(f'grafana dashboard must start with operator guide text panel: {path}')
        missing = titles - {panel.get('title') for panel in dash.get('panels', [])}
        if missing:
            raise SystemExit(f'grafana dashboard template missing user-facing panels {missing}: {path}')
        for panel in dash.get('panels', []):
            _validate_panel(path, panel)
    _validate_query_regressions()


def _validate_panel(path: str, panel: dict[str, object]) -> None:
    description = str(panel.get('description', ''))
    title = panel.get('title')
    if not description:
        raise SystemExit(f'grafana panel missing operator description: {path}::{title}')
    tokens = ['Healthy', 'Attention', 'Action Required', 'No Runtime Data', 'No Data', 'Action']
    if not any(token in description for token in tokens):
        raise SystemExit(f'grafana panel description must include status/action guidance: {path}::{title}')
    if panel.get('datasource') != {'type': 'prometheus', 'uid': '${datasource}'}:
        raise SystemExit(f'grafana panel must use datasource variable: {path}::{title}')


def _validate_query_regressions() -> None:
    exec_text = json.dumps(read_json('ops/grafana/dashboards/executive_runtime_overview.json'), ensure_ascii=False)
    if 'clamp_min(sum(rate(http_requests_total' in exec_text:
        raise SystemExit('executive dashboard must not clamp low-traffic rate denominators to 1')
    chat_text = json.dumps(read_json('ops/grafana/dashboards/chat_api_deep_dive.json'), ensure_ascii=False)
    for metric in ['streaming_chunks_total', 'streaming_bytes_total', 'streaming_errors_total', 'streaming_usage_events_total']:
        if metric not in chat_text:
            raise SystemExit(f'chat streaming dashboard missing metric: {metric}')
    if 'path=~\\"chat/completions(:stream)?\\"' not in chat_text:
        raise SystemExit('chat API dashboard upstream p95 must use actual upstream_request_duration_seconds path labels')
