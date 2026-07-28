from __future__ import annotations

import json

from ai_model_serving.domain import ModelRegistry
from ai_model_serving.governance_validation.common import ROOT, read_json, read_yaml
from ai_model_serving.monitoring_projection import monitoring_projection_document

GPU_DASHBOARD = 'ops/grafana/dashboards/gpu_capacity_and_oom_risk.json'


def validate_grafana_dashboard_templates() -> None:
    monitoring = read_yaml('configs/monitoring.yaml')
    grafana = monitoring.get('monitoring_stack', {}).get('grafana', {})
    contracts = grafana.get('dashboard_contracts', [])
    if not contracts:
        raise SystemExit('monitoring config must define grafana dashboard_contracts')
    expected_variables = set(grafana.get('dashboard_variables', []))
    for contract in contracts:
        path = str(contract.get('path', ''))
        titles = {str(title) for title in contract.get('required_panels', [])}
        if not path or not titles:
            raise SystemExit(f'grafana dashboard contract must define path and required_panels: {contract}')
        dash = read_json(path)
        if not dash.get('uid') or 'contract-reference' not in dash.get('tags', []):
            raise SystemExit(f'grafana dashboard template missing reference metadata: {path}')
        variables = {item.get('name') for item in dash.get('templating', {}).get('list', [])}
        if not expected_variables.issubset(variables):
            raise SystemExit(f'grafana dashboard missing variables {expected_variables - variables}: {path}')
        if ' / ' in str(dash.get('title', '')):
            raise SystemExit(f'grafana dashboard title must be English-only: {path}')
        if not dash.get('panels'):
            raise SystemExit(f'grafana dashboard has no panels: {path}')
        panel_titles = {panel.get('title') for panel in _iter_panels(dash.get('panels', []))}
        missing = titles - panel_titles
        if missing:
            raise SystemExit(f'grafana dashboard template missing user-facing panels {missing}: {path}')
        _validate_collapsed_detail_rows(path, dash, contract)
        for panel in _iter_panels(dash.get('panels', [])):
            _validate_panel(path, panel)
        _validate_kv_cache_ratio_axis(path, dash)
    _validate_gpu_dashboard()


def _validate_gpu_dashboard() -> None:
    """Validate the single surviving GPU Capacity and OOM Risk dashboard."""
    gpu = read_json(GPU_DASHBOARD)
    gpu_text = json.dumps(gpu, ensure_ascii=False)
    if 'backend_restart_total' in gpu_text or 'gpu_oom_events_total' in gpu_text:
        raise SystemExit(
            f'{GPU_DASHBOARD} still references retired metric names (backend_restart_total/'
            'gpu_oom_events_total) -- it must use the documented cAdvisor metrics '
            '(container_oom_events_total/container_start_time_seconds) instead'
        )
    if 'container_oom_events_total' not in gpu_text or 'container_start_time_seconds' not in gpu_text:
        raise SystemExit(
            f'{GPU_DASHBOARD} is missing the cAdvisor OOM/restart source metrics '
            '(container_oom_events_total and/or container_start_time_seconds)'
        )
    if 'Home dashboard — GPU' in gpu_text or 'Home dashboard - GPU' in gpu_text:
        raise SystemExit(f'{GPU_DASHBOARD} has a stale "Home dashboard — GPU" tooltip string left in it')
    _validate_oom_restart_sources(gpu_text)


def _iter_panels(panels: list[dict[str, object]]) -> object:
    for panel in panels:
        yield panel
        yield from _iter_panels(panel.get('panels', []))  # type: ignore[arg-type]


def _validate_collapsed_detail_rows(path: str, dashboard: dict[str, object], contract: dict[str, object]) -> None:
    expected_rows = {str(title) for title in contract.get('collapsed_detail_rows', [])}
    if not expected_rows:
        return
    rows = {
        str(panel.get('title')): panel
        for panel in dashboard.get('panels', [])
        if panel.get('type') == 'row'
    }
    missing = expected_rows - set(rows)
    if missing:
        raise SystemExit(f'grafana dashboard missing collapsed detail rows {missing}: {path}')
    for title in expected_rows:
        row = rows[title]
        if row.get('collapsed') is not True or not row.get('panels'):
            raise SystemExit(f'grafana detail row must be collapsed and own nested panels: {path}::{title}')


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


def _validate_kv_cache_ratio_axis(path: str, dashboard: dict[str, object]) -> None:
    for panel in _iter_panels(dashboard.get('panels', [])):
        if panel.get('type') not in {'stat', 'timeseries'}:
            continue
        target_text = json.dumps(panel.get('targets', []), ensure_ascii=False)
        if 'vllm_kv_cache_usage_ratio' not in target_text:
            continue
        defaults = panel.get('fieldConfig', {}).get('defaults', {})
        if defaults.get('unit') != 'percentunit' or defaults.get('min') != 0 or defaults.get('max') != 1:
            title = panel.get('title')
            raise SystemExit(
                f'{path}::{title} must pin vllm_kv_cache_usage_ratio axis to percentunit min=0 max=1'
            )


def _monitoring_projection() -> dict[str, object]:
    monitoring = read_yaml('configs/monitoring.yaml')
    registry = ModelRegistry(read_yaml('configs/model_catalog.yaml'), read_yaml('configs/model_serving.yaml'))
    return monitoring_projection_document(registry=registry, monitoring=monitoring)


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
        raise SystemExit(
            f'configs/monitoring.yaml metric_sources.container_signal_sources.critical_container_regex'
            f'={container_sources.get("critical_container_regex")!r} does not match the projected '
            f'critical_container_signal_regex={critical_regex!r} (derived from ModelRegistry)'
        )
    if container_sources.get('vllm_container_regex') != vllm_regex:
        raise SystemExit(
            f'configs/monitoring.yaml metric_sources.container_signal_sources.vllm_container_regex'
            f'={container_sources.get("vllm_container_regex")!r} does not match the projected '
            f'vllm_container_regex={vllm_regex!r} (derived from ModelRegistry)'
        )
    cadvisor_metrics = set(vllm_containers.get('required_metrics', []))
    source_metrics = set(container_sources.get('source_metrics', []))
    rules_text = (ROOT / 'ops/prometheus/rules/model_runtime.rules.yml').read_text(encoding='utf-8')
    docs_text = (ROOT / 'docs/operations/monitoring_ux.md').read_text(encoding='utf-8')
    defined = cadvisor_metrics | source_metrics
    for metric in required:
        if metric not in defined and metric not in rules_text and metric not in docs_text:
            raise SystemExit(f'OOM/restart dashboard metric lacks source definition: {metric}')
    if critical_regex not in gpu_text:
        raise SystemExit(
            f'{GPU_DASHBOARD} does not contain the projected critical_container_signal_regex '
            f'{critical_regex!r} (from configs/monitoring.yaml via ModelRegistry) -- its panel query regex is stale'
        )
    for metric in required:
        if metric not in gpu_text or metric not in docs_text:
            raise SystemExit(f'{metric} must appear in the GPU/OOM dashboard and docs')
