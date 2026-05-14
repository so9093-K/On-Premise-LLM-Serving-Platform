from __future__ import annotations

import json
import re

from ai_model_serving.domain import ModelRegistry
from ai_model_serving.governance_validation.common import ROOT, read_json, read_yaml
from ai_model_serving.monitoring_projection import monitoring_projection_document


def validate_grafana_dashboard_templates() -> None:
    required_titles = {
        'ops/grafana/dashboards/serving_cockpit.json': {
            'Compact Operator Banner', 'Overall Status', 'User Requests in Window',
            'Model/Gateway Ready', 'Prometheus Target Health', 'Min GPU Headroom',
            'Container OOM / Restart Signals', 'Warm Readiness Evidence', 'Gateway Ready',
            'Risk Adapter Ready', 'vLLM Runtime Scrape', 'DCGM Exporter Up',
            'cAdvisor Up', 'Request Rate by Service/Route', 'TTFT p50/p95/p99',
            'HTTP Handler Latency p95/p99', 'Error Request Rate by Service/Route/Status',
            'Client Disconnect Rate', 'vLLM Queue Depth', 'KV Cache Pressure',
            'Token Throughput', 'GPU Utilization', 'GPU Power',
            'Current GPU Memory Used', 'Max GPU Memory Used in Range',
            'Current GPU Headroom', 'Min GPU Headroom in Range',
            'VRAM Used vs Budget', 'Max KV Cache Pressure in Range',
            'GPU Temperature', 'Container OOM / Restart Signals Detail',
            'Prometheus Target Health Detail', 'Scrape Target Count',
            'Scrape Up Count', 'Drill-down Links',
        },
        'ops/grafana/dashboards/executive_runtime_overview.json': {
            'Overall Status', 'User Traffic', 'p95 Latency (5m)', 'Error Rate',
            'GPU Headroom', 'Component Readiness', 'Scrape Health',
        },
        'ops/grafana/dashboards/gpu_capacity_and_oom_risk.json': {
            'GPU Headroom', 'GPU Memory Used', 'Container OOM or Restart Signals',
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
        if path == 'ops/grafana/dashboards/serving_cockpit.json' and 'user_route' not in variables:
            raise SystemExit('serving cockpit dashboard must define user_route variable')
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
    cockpit = read_json('ops/grafana/dashboards/serving_cockpit.json')
    cockpit_text = json.dumps(cockpit, ensure_ascii=False)
    user_route = next(
        (item for item in cockpit.get('templating', {}).get('list', []) if item.get('name') == 'user_route'),
        {},
    )
    user_route_text = json.dumps(user_route, ensure_ascii=False)
    if '/v1/risk/.*' not in user_route_text:
        raise SystemExit('serving cockpit user_route must include /v1/risk/.*')
    if re.search(r'(?<!/v1)/risk/\.\*', user_route_text):
        raise SystemExit('serving cockpit user_route must not use stale /risk/.* regex')
    if 'Request Rate by Route/Model' in cockpit_text:
        raise SystemExit('serving cockpit must not claim model-level request rate without a model label')
    if 'E2E Latency p95/p99' in cockpit_text:
        raise SystemExit('serving cockpit must use HTTP Handler Latency title for http_request_duration_seconds')
    if 'Error Rate by Route/Status' in cockpit_text:
        raise SystemExit('serving cockpit error request rate panel must not be titled as a ratio')
    for route in ['/health', '/ready', '/metrics', '/docs', '/openapi.json']:
        if route not in cockpit_text:
            raise SystemExit(f'serving cockpit user traffic exclusion missing route: {route}')
    if 'service=~\\"gateway|risk-adapter\\"' not in cockpit_text:
        raise SystemExit('serving cockpit user-impact queries must include gateway and risk-adapter services')
    panels = {panel.get('title'): panel for panel in cockpit.get('panels', [])}
    user_requests = json.dumps(panels.get('User Requests in Window', {}), ensure_ascii=False)
    if 'service=\\"gateway\\"' not in user_requests:
        raise SystemExit('serving cockpit User Requests in Window must count gateway public entrypoint only')
    if 'status_code=~\\"$status_code\\"' in user_requests:
        raise SystemExit('serving cockpit User Requests in Window must not filter the total by status_code')
    service_activity = json.dumps(panels.get('Request Rate by Service/Route', {}), ensure_ascii=False)
    if 'by (service, route)' not in service_activity or 'service=~\\"gateway|risk-adapter\\"' not in service_activity:
        raise SystemExit('serving cockpit service activity panel must split gateway/risk-adapter by service label')
    _validate_serving_cockpit_projected_constants(cockpit_text, panels)
    if 'backend_restart_total' in cockpit_text or 'gpu_oom_events_total' in cockpit_text:
        raise SystemExit('serving cockpit must use documented cAdvisor OOM/restart source metrics')
    if 'container_oom_events_total' not in cockpit_text or 'container_start_time_seconds' not in cockpit_text:
        raise SystemExit('serving cockpit missing cAdvisor OOM/restart source metrics')
    user_route_options = {str(item.get('text')): str(item.get('value')) for item in user_route.get('options', [])}
    expected_user_route_options = {
        'All user routes': '/v1/chat/completions|/v1/embeddings|/v1/risk/.*',
        'Chat': '/v1/chat/completions',
        'Embeddings': '/v1/embeddings',
        'Risk': '/v1/risk/.*',
    }
    if user_route_options != expected_user_route_options:
        raise SystemExit(f'serving cockpit user_route options mismatch: {user_route_options}')
    warm_readiness = json.dumps(panels.get('Warm Readiness Evidence', {}), ensure_ascii=False)
    if 'freshness requires ai_readiness_last_checked_timestamp_seconds or synthetic probes' not in warm_readiness:
        raise SystemExit('Warm Readiness Evidence must document readiness freshness limitation')
    gpu_text = json.dumps(read_json('ops/grafana/dashboards/gpu_capacity_and_oom_risk.json'), ensure_ascii=False)
    if 'backend_restart_total' in gpu_text or 'gpu_oom_events_total' in gpu_text:
        raise SystemExit('GPU/OOM dashboard must use documented cAdvisor OOM/restart source metrics')
    _validate_oom_restart_sources(gpu_text)
    _validate_status_board_navigation()
    for dash_path in [
        'ops/grafana/dashboards/executive_runtime_overview.json',
        'ops/grafana/dashboards/gpu_capacity_and_oom_risk.json',
        'ops/grafana/dashboards/risk_signal_operations.json',
        'ops/grafana/dashboards/chat_api_deep_dive.json',
        'ops/grafana/dashboards/model_runtime_deep_dive.json',
    ]:
        text = json.dumps(read_json(dash_path), ensure_ascii=False)
        if 'Home dashboard — GPU' in text or 'Home dashboard - GPU' in text:
            raise SystemExit(f'grafana dashboard has stale GPU home tooltip: {dash_path}')


def _monitoring_projection() -> dict[str, object]:
    monitoring = read_yaml('configs/monitoring.yaml')
    registry = ModelRegistry(read_yaml('configs/model_catalog.yaml'), read_yaml('configs/model_serving.yaml'))
    return monitoring_projection_document(registry=registry, monitoring=monitoring)


def _validate_serving_cockpit_projected_constants(cockpit_text: str, panels: dict[object, object]) -> None:
    projection = _monitoring_projection()
    expected_count = projection.get('observability_trust', {}).get('expected_critical_target_count')
    count_expr = f'>= bool {expected_count}'
    if count_expr not in cockpit_text:
        raise SystemExit(f'serving cockpit expected scrape target count must match projection: {expected_count}')
    for title in ['Prometheus Target Health', 'Scrape Up Count', 'Warm Readiness Evidence']:
        description = str(panels.get(title, {}).get('description', ''))
        if 'projected from gateway + risk-adapter + vLLM runtime targets + dcgm + cadvisor' not in description:
            raise SystemExit(f'serving cockpit panel must explain projected expected target count: {title}')


def _validate_oom_restart_sources(gpu_text: str) -> None:
    monitoring = read_yaml('configs/monitoring.yaml')
    projection = _monitoring_projection()
    container_projection = projection.get('container_signals', {})
    critical_regex = str(container_projection.get('critical_container_signal_regex', ''))
    vllm_regex = str(container_projection.get('vllm_container_regex', ''))
    required = {'container_oom_events_total', 'container_start_time_seconds'}
    vllm_containers = monitoring.get('metric_sources', {}).get('vllm_containers', {})
    container_sources = monitoring.get('metric_sources', {}).get('container_signal_sources', {})
    if container_sources.get('critical_container_regex') != critical_regex:
        raise SystemExit('container signal critical regex must match monitoring projection')
    if container_sources.get('vllm_container_regex') != vllm_regex:
        raise SystemExit('container signal vLLM regex must match monitoring projection')
    cadvisor_metrics = set(vllm_containers.get('required_metrics', []))
    source_metrics = set(container_sources.get('source_metrics', []))
    rules_text = (ROOT / 'ops/prometheus/rules/model_runtime.rules.yml').read_text(encoding='utf-8')
    docs_text = (ROOT / 'docs/operations/monitoring_ux.md').read_text(encoding='utf-8')
    for phrase in ['공통 dashboard variable', 'Serving Cockpit 전용 variable', 'IDLE WARM은 “readiness and scrape evidence exists”']:
        if phrase not in docs_text:
            raise SystemExit(f'monitoring UX doc missing Serving Cockpit source/variable phrase: {phrase}')
    defined = cadvisor_metrics | source_metrics
    for metric in required:
        if metric not in defined and metric not in rules_text and metric not in docs_text:
            raise SystemExit(f'OOM/restart dashboard metric lacks source definition: {metric}')
    cockpit_text = json.dumps(read_json('ops/grafana/dashboards/serving_cockpit.json'), ensure_ascii=False)
    for text, label in [(cockpit_text, 'serving cockpit'), (gpu_text, 'GPU/OOM dashboard'), (docs_text, 'monitoring UX doc')]:
        if critical_regex not in text:
            raise SystemExit(f'{label} OOM/restart regex must match critical_container_signal_regex')
    for metric in required:
        if metric not in cockpit_text or metric not in gpu_text or metric not in docs_text:
            raise SystemExit(f'{metric} must appear in dashboard and docs')


def _validate_status_board_navigation() -> None:
    expected = {
        'serving_cockpit': [
            'gpu_capacity_and_oom_risk',
            'executive_runtime_overview',
            'chat_api_deep_dive',
            'model_runtime_deep_dive',
            'risk_signal_operations',
        ],
        'gpu_capacity_and_oom_risk': ['serving_cockpit', 'executive_runtime_overview'],
        'executive_runtime_overview': [
            'serving_cockpit',
            'gpu_capacity_and_oom_risk',
            'chat_api_deep_dive',
            'model_runtime_deep_dive',
            'risk_signal_operations',
        ],
        'chat_api_deep_dive': ['serving_cockpit', 'executive_runtime_overview', 'model_runtime_deep_dive'],
        'model_runtime_deep_dive': ['serving_cockpit', 'gpu_capacity_and_oom_risk', 'chat_api_deep_dive'],
        'risk_signal_operations': ['serving_cockpit', 'executive_runtime_overview'],
    }
    title_to_uid = {
        'Serving Cockpit': 'serving_cockpit',
        'GPU Capacity and OOM Risk': 'gpu_capacity_and_oom_risk',
        'Executive Runtime Overview': 'executive_runtime_overview',
        'Chat API Deep Dive': 'chat_api_deep_dive',
        'Model Runtime Deep Dive': 'model_runtime_deep_dive',
        'Risk Signal Operations': 'risk_signal_operations',
    }
    status_doc = (ROOT / 'docs/operations/status_board_ux.md').read_text(encoding='utf-8')
    monitoring_doc = (ROOT / 'docs/operations/monitoring_ux.md').read_text(encoding='utf-8')
    for uid, expected_links in expected.items():
        dash = read_json(f'ops/grafana/dashboards/{uid}.json')
        actual = [title_to_uid.get(link.get('title', ''), '') for link in dash.get('links', [])]
        if actual != expected_links:
            raise SystemExit(f'grafana navigation mismatch for {uid}: {actual} != {expected_links}')
        doc_line = f'{uid} → {", ".join(expected_links)}'
        if doc_line not in status_doc:
            raise SystemExit(f'status board UX missing navigation line: {doc_line}')
        table_line = f'| `{uid}` | `{ "`, `".join(expected_links) }` |'
        if table_line not in monitoring_doc:
            raise SystemExit(f'monitoring UX missing navigation table line: {table_line}')
