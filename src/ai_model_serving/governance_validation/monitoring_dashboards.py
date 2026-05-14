from __future__ import annotations

import json
import re

from ai_model_serving.domain import ModelRegistry
from ai_model_serving.governance_validation.common import ROOT, read_json, read_yaml
from ai_model_serving.monitoring_projection import monitoring_projection_document


def validate_grafana_dashboard_templates() -> None:
    required_titles = {
        'ops/grafana/dashboards/serving_home.json': {
            'Serving Home Operator Guide', 'Serving Verdict', 'State', 'User Traffic',
            'Scrape Targets', 'GPU Headroom', 'OOM / Restart', 'Runtime Capacity',
            'Needs Attention', 'Min GPU Headroom', 'Max KV Cache Pressure',
            'Container OOM / Restart Signals', 'VRAM Used vs Budget',
            'vLLM Queue Depth', 'KV Cache Pressure', 'Token Throughput',
            'GPU Utilization', 'Drill-down Links',
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
        'ops/grafana/dashboards/api_experience.json': {
            'Chat Request Rate', 'Streaming Errors', 'Usage Chunk Events',
        },
        'ops/grafana/dashboards/model_runtime_deep_dive.json': {
            'Token Throughput', 'Queue Depth', 'KV Cache Usage',
        },
        'ops/grafana/dashboards/observability_data_quality.json': {
            'Visible Targets vs Expected', 'Up Targets vs Visible',
            'Missing Critical Targets', 'Missing Target Detection',
            'Down Target Detection',
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
        if path == 'ops/grafana/dashboards/serving_home.json' and 'user_route' not in variables:
            raise SystemExit('serving home dashboard must define user_route variable')
        if ' / ' in str(dash.get('title', '')):
            raise SystemExit(f'grafana dashboard title must be English-only: {path}')
        if not dash.get('panels') or dash['panels'][0].get('type') != 'text':
            raise SystemExit(f'grafana dashboard must start with operator guide text panel: {path}')
        missing = titles - {panel.get('title') for panel in dash.get('panels', [])}
        if missing:
            raise SystemExit(f'grafana dashboard template missing user-facing panels {missing}: {path}')
        for panel in dash.get('panels', []):
            _validate_panel(path, panel)
    _validate_serving_home_constraints()
    _validate_query_regressions()


def _validate_serving_home_constraints() -> None:
    home = read_json('ops/grafana/dashboards/serving_home.json')
    panels = home.get('panels', [])
    panel_count = len(panels)
    if panel_count > 20:
        raise SystemExit(f'serving home dashboard must have at most 20 panels, found {panel_count}')
    panel_titles = {panel.get('title') for panel in panels}
    if 'Needs Attention' not in panel_titles:
        raise SystemExit('serving home dashboard must have a Needs Attention panel')
    backlog_indicators = ['Backlog', 'TODO', 'instrumentation plan', 'backlog']
    home_text = json.dumps(home, ensure_ascii=False)
    for indicator in backlog_indicators:
        if f'"title": "{indicator}' in home_text:
            raise SystemExit(f'serving home dashboard must not contain Backlog/TODO panels: {indicator}')
    # User Traffic must not use red for zero — no colorMode=background with red at null threshold
    user_traffic_panel = next((p for p in panels if p.get('title') == 'User Traffic'), None)
    if user_traffic_panel:
        steps = (user_traffic_panel.get('fieldConfig', {})
                 .get('defaults', {}).get('thresholds', {}).get('steps', []))
        if steps and steps[0].get('color') == 'red':
            raise SystemExit('serving home User Traffic panel must not use red as the base (null) threshold color')
    # Raw GPU Memory Used must not be a top-level red background stat
    forbidden_titles = {'Current GPU Memory Used', 'Max GPU Memory Used in Range'}
    for panel in panels:
        if panel.get('title') in forbidden_titles:
            color_mode = panel.get('options', {}).get('colorMode', '')
            steps = (panel.get('fieldConfig', {})
                     .get('defaults', {}).get('thresholds', {}).get('steps', []))
            if color_mode == 'background' and steps and steps[0].get('color') == 'red':
                raise SystemExit(
                    f'serving home must not show raw GPU memory used as a red background card: {panel["title"]}'
                )
    # OOM/restart queries must not use or vector(0) — absence must not be hidden
    oom_panel = next((p for p in panels if 'OOM' in (p.get('title') or '')), None)
    if oom_panel:
        oom_text = json.dumps(oom_panel, ensure_ascii=False)
        if 'or vector(0)' in oom_text and 'container_oom_events_total' in oom_text:
            raise SystemExit('OOM/restart panel must not use or vector(0) — cAdvisor absence must not be hidden as 0')
    # Verdict banner must use ai_serving_verdict_code
    verdict_panel = next((p for p in panels if p.get('title') == 'Serving Verdict'), None)
    if verdict_panel:
        verdict_text = json.dumps(verdict_panel, ensure_ascii=False)
        if 'ai_serving_verdict_code' not in verdict_text:
            raise SystemExit('serving home Serving Verdict panel must use ai_serving_verdict_code')


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
    api_text = json.dumps(read_json('ops/grafana/dashboards/api_experience.json'), ensure_ascii=False)
    for metric in ['streaming_chunks_total', 'streaming_bytes_total', 'streaming_errors_total', 'streaming_usage_events_total']:
        if metric not in api_text:
            raise SystemExit(f'api experience dashboard missing metric: {metric}')
    if 'path=~\\"chat/completions(:stream)?\\"' not in api_text:
        raise SystemExit('api experience dashboard upstream p95 must use actual upstream_request_duration_seconds path labels')
    home = read_json('ops/grafana/dashboards/serving_home.json')
    home_text = json.dumps(home, ensure_ascii=False)
    user_route = next(
        (item for item in home.get('templating', {}).get('list', []) if item.get('name') == 'user_route'),
        {},
    )
    user_route_text = json.dumps(user_route, ensure_ascii=False)
    if '/v1/risk/.*' not in user_route_text:
        raise SystemExit('serving home user_route must include /v1/risk/.*')
    if re.search(r'(?<!/v1)/risk/\.\*', user_route_text):
        raise SystemExit('serving home user_route must not use stale /risk/.* regex')
    for route in ['/health', '/ready', '/metrics', '/docs', '/openapi.json']:
        if route not in home_text:
            raise SystemExit(f'serving home user traffic exclusion missing route: {route}')
    panels = {panel.get('title'): panel for panel in home.get('panels', [])}
    user_traffic = json.dumps(panels.get('User Traffic', {}), ensure_ascii=False)
    if 'service=\\"gateway\\"' not in user_traffic:
        raise SystemExit('serving home User Traffic must count gateway public entrypoint only')
    if 'status_code=~\\"$status_code\\"' in user_traffic:
        raise SystemExit('serving home User Traffic must not filter the total by status_code')
    _validate_serving_home_projected_constants(home_text, panels)
    if 'backend_restart_total' in home_text or 'gpu_oom_events_total' in home_text:
        raise SystemExit('serving home must use documented cAdvisor OOM/restart source metrics')
    if 'container_oom_events_total' not in home_text or 'container_start_time_seconds' not in home_text:
        raise SystemExit('serving home missing cAdvisor OOM/restart source metrics')
    user_route_options = {str(item.get('text')): str(item.get('value')) for item in user_route.get('options', [])}
    expected_user_route_options = {
        'All user routes': '/v1/chat/completions|/v1/embeddings|/v1/risk/.*',
        'Chat': '/v1/chat/completions',
        'Embeddings': '/v1/embeddings',
        'Risk': '/v1/risk/.*',
    }
    if user_route_options != expected_user_route_options:
        raise SystemExit(f'serving home user_route options mismatch: {user_route_options}')
    gpu_text = json.dumps(read_json('ops/grafana/dashboards/gpu_capacity_and_oom_risk.json'), ensure_ascii=False)
    if 'backend_restart_total' in gpu_text or 'gpu_oom_events_total' in gpu_text:
        raise SystemExit('GPU/OOM dashboard must use documented cAdvisor OOM/restart source metrics')
    _validate_oom_restart_sources(gpu_text)
    _validate_status_board_navigation()
    for dash_path in [
        'ops/grafana/dashboards/executive_runtime_overview.json',
        'ops/grafana/dashboards/gpu_capacity_and_oom_risk.json',
        'ops/grafana/dashboards/risk_signal_operations.json',
        'ops/grafana/dashboards/api_experience.json',
        'ops/grafana/dashboards/model_runtime_deep_dive.json',
        'ops/grafana/dashboards/observability_data_quality.json',
    ]:
        text = json.dumps(read_json(dash_path), ensure_ascii=False)
        if 'Home dashboard — GPU' in text or 'Home dashboard - GPU' in text:
            raise SystemExit(f'grafana dashboard has stale GPU home tooltip: {dash_path}')
    # Streaming status contract: dashboard queries for streaming_duration_seconds and
    # streaming_requests_total must not reference old uppercase status codes.
    for dash_path in [
        'ops/grafana/dashboards/api_experience.json',
        'ops/grafana/dashboards/serving_home.json',
    ]:
        text = json.dumps(read_json(dash_path), ensure_ascii=False)
        if 'status=\\"COMPLETED\\"' in text or 'status=\\"ERROR\\"' in text:
            raise SystemExit(f'dashboard must not use uppercase streaming status labels: {dash_path}')


def _monitoring_projection() -> dict[str, object]:
    monitoring = read_yaml('configs/monitoring.yaml')
    registry = ModelRegistry(read_yaml('configs/model_catalog.yaml'), read_yaml('configs/model_serving.yaml'))
    return monitoring_projection_document(registry=registry, monitoring=monitoring)


def _validate_serving_home_projected_constants(home_text: str, panels: dict[object, object]) -> None:
    projection = _monitoring_projection()
    expected_count = projection.get('observability_trust', {}).get('expected_critical_target_count')
    count_expr = f'>= bool {expected_count}'
    # Serving Home uses threshold-based coloring (not bool expr) for scrape targets,
    # so check the description phrases instead of the PromQL expression.
    for title in ['Scrape Targets', 'Visible Targets vs Expected']:
        description = str(panels.get(title, {}).get('description', ''))
        if description and 'projected from gateway + risk-adapter + vLLM runtime targets + dcgm + cadvisor' not in description:
            raise SystemExit(f'serving home panel must explain projected expected target count: {title}')
    # Verify Observability / Data Quality dashboard uses the bool count expression
    obs_text = json.dumps(read_json('ops/grafana/dashboards/observability_data_quality.json'), ensure_ascii=False)
    obs_desc = json.dumps(
        [p.get('description', '') for p in read_json('ops/grafana/dashboards/observability_data_quality.json').get('panels', [])],
        ensure_ascii=False,
    )
    if 'projected from gateway + risk-adapter + vLLM runtime targets + dcgm + cadvisor' not in obs_desc:
        raise SystemExit('observability_data_quality panels must explain projected expected target count')


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
    for phrase in ['공통 dashboard variable', 'Serving Home 전용 variable', 'IDLE WARM은 "readiness and scrape evidence exists"']:
        if phrase not in docs_text:
            raise SystemExit(f'monitoring UX doc missing Serving Home source/variable phrase: {phrase}')
    defined = cadvisor_metrics | source_metrics
    for metric in required:
        if metric not in defined and metric not in rules_text and metric not in docs_text:
            raise SystemExit(f'OOM/restart dashboard metric lacks source definition: {metric}')
    home_text = json.dumps(read_json('ops/grafana/dashboards/serving_home.json'), ensure_ascii=False)
    for text, label in [(home_text, 'serving home'), (gpu_text, 'GPU/OOM dashboard'), (docs_text, 'monitoring UX doc')]:
        if critical_regex not in text:
            raise SystemExit(f'{label} OOM/restart regex must match critical_container_signal_regex')
    for metric in required:
        if metric not in home_text or metric not in gpu_text or metric not in docs_text:
            raise SystemExit(f'{metric} must appear in dashboard and docs')


def _validate_status_board_navigation() -> None:
    expected = {
        'serving_home': [
            'gpu_capacity_and_oom_risk',
            'executive_runtime_overview',
            'api_experience',
            'model_runtime_deep_dive',
            'risk_signal_operations',
            'observability_data_quality',
        ],
        'gpu_capacity_and_oom_risk': ['serving_home', 'executive_runtime_overview'],
        'executive_runtime_overview': [
            'serving_home',
            'gpu_capacity_and_oom_risk',
            'api_experience',
            'model_runtime_deep_dive',
            'risk_signal_operations',
        ],
        'api_experience': ['serving_home', 'executive_runtime_overview', 'model_runtime_deep_dive'],
        'model_runtime_deep_dive': ['serving_home', 'gpu_capacity_and_oom_risk', 'api_experience'],
        'risk_signal_operations': ['serving_home', 'executive_runtime_overview'],
        'observability_data_quality': ['serving_home', 'executive_runtime_overview'],
    }
    title_to_uid = {
        'Serving Home': 'serving_home',
        'GPU Capacity and OOM Risk': 'gpu_capacity_and_oom_risk',
        'Executive Runtime Overview': 'executive_runtime_overview',
        'API Experience': 'api_experience',
        'Model Runtime Deep Dive': 'model_runtime_deep_dive',
        'Risk Signal Operations': 'risk_signal_operations',
        'Observability / Data Quality': 'observability_data_quality',
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
