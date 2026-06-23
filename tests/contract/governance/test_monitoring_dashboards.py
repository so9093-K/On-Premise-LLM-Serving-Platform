from __future__ import annotations

from collections.abc import Iterator

from .helpers import *  # noqa: F401,F403

GPU_DASHBOARD = "ops/grafana/dashboards/gpu_capacity_and_oom_risk.json"
USAGE_DASHBOARD = "ops/grafana/dashboards/usage_today.json"


def iter_panels(panels: list[dict]) -> Iterator[dict]:
    for panel in panels:
        yield panel
        yield from iter_panels(panel.get("panels", []))


def grafana_contract(path: str) -> dict:
    monitoring = yaml.safe_load((ROOT / "configs/monitoring.yaml").read_text(encoding="utf-8"))
    contracts = monitoring["monitoring_stack"]["grafana"]["dashboard_contracts"]
    for contract in contracts:
        if contract["path"] == path:
            return contract
    raise AssertionError(f"missing grafana dashboard contract: {path}")


def test_monitoring_dashboards_are_the_two_curated_boards() -> None:
    dashboards = {path.name for path in (ROOT / "ops/grafana/dashboards").glob("*.json")}
    assert dashboards == {"gpu_capacity_and_oom_risk.json", "usage_today.json"}
    monitoring = yaml.safe_load((ROOT / "configs/monitoring.yaml").read_text(encoding="utf-8"))
    assert "operator_status_ux" not in monitoring
    contracts = monitoring["monitoring_stack"]["grafana"]["dashboard_contracts"]
    assert {contract["path"] for contract in contracts} == {GPU_DASHBOARD, USAGE_DASHBOARD}
    ux_ids = {item["id"] for item in monitoring["ux_dashboards"]}
    assert ux_ids == {"gpu_capacity_and_oom_risk", "usage_today"}


def test_usage_dashboard_required_panels_present_and_glanceable() -> None:
    dashboard = json.loads((ROOT / USAGE_DASHBOARD).read_text(encoding="utf-8"))
    assert dashboard["uid"] == "usage_today"
    assert " / " not in dashboard["title"]
    variables = {item["name"] for item in dashboard["templating"]["list"]}
    assert {"datasource", "window"}.issubset(variables)
    required = set(grafana_contract(USAGE_DASHBOARD)["required_panels"])
    titles = {panel["title"] for panel in iter_panels(dashboard["panels"])}
    assert required.issubset(titles)
    for panel in iter_panels(dashboard["panels"]):
        description = panel.get("description", "")
        assert description
        assert any(
            token in description
            for token in ["Healthy", "Attention", "Action Required", "No Runtime Data", "No Data", "Action"]
        )


def test_gpu_dashboard_required_panels_present() -> None:
    dashboard = json.loads((ROOT / GPU_DASHBOARD).read_text(encoding="utf-8"))
    required = set(grafana_contract(GPU_DASHBOARD)["required_panels"])
    assert required.issubset({panel["title"] for panel in iter_panels(dashboard["panels"])})


def test_gpu_dashboard_panels_include_operator_descriptions() -> None:
    dashboard = json.loads((ROOT / GPU_DASHBOARD).read_text(encoding="utf-8"))
    for panel in iter_panels(dashboard["panels"]):
        description = panel.get("description", "")
        assert description
        assert any(
            token in description
            for token in ["Healthy", "Attention", "Action Required", "No Runtime Data", "No Data", "Action"]
        )


def test_gpu_dashboard_is_english_titled_and_variable_backed() -> None:
    dashboard = json.loads((ROOT / GPU_DASHBOARD).read_text(encoding="utf-8"))
    assert " / " not in dashboard["title"]
    variables = {item["name"] for item in dashboard["templating"]["list"]}
    assert {"datasource", "window"}.issubset(variables)
    assert dashboard["panels"]
    for panel in dashboard["panels"]:
        if panel["type"] == "stat" and panel.get("options", {}).get("colorMode") == "background":
            assert panel["options"].get("graphMode") == "none"
        if panel["type"] == "timeseries":
            assert panel.get("options", {}).get("legend", {}).get("showLegend") is True
    gpu_text = json.dumps(dashboard, ensure_ascii=False)
    assert "backend_restart_total" not in gpu_text
    assert "gpu_oom_events_total" not in gpu_text
    assert "container_oom_events_total" in gpu_text
    assert "container_start_time_seconds" in gpu_text


def test_endpoint_reference_lists_gpu_dashboard() -> None:
    endpoint_doc = (ROOT / "docs/operations/endpoint_reference.md").read_text(encoding="utf-8")
    assert "`gpu_capacity_and_oom_risk`" in endpoint_doc
    assert "clamp_min(sum(rate(http_requests_total" not in endpoint_doc
    assert "allowUiUpdates=false" in endpoint_doc


def test_dashboard_navigation_links_resolve_to_existing_uids() -> None:
    dashboards = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / "ops/grafana/dashboards").glob("*.json")
    }
    valid_uids = {d["uid"] for d in dashboards.values()}
    for name, dashboard in dashboards.items():
        for link in dashboard.get("links", []):
            url = link.get("url", "")
            assert "/d/" in url, f"Dashboard '{name}' link has unexpected URL format: {url}"
            target_uid = url.split("/d/")[-1].split("/")[0]
            assert target_uid in valid_uids, (
                f"Dashboard '{name}' links to unknown uid '{target_uid}' (url={url})"
            )


def test_monitoring_ux_streaming_label_is_status_not_result() -> None:
    monitoring_ux = (ROOT / "docs/operations/monitoring_ux.md").read_text(encoding="utf-8")
    assert "target,result}" not in monitoring_ux, \
        "monitoring_ux.md must use 'status' label, not 'result', for streaming metrics"
    assert ",result}" not in monitoring_ux, \
        "monitoring_ux.md must use 'status' label, not 'result', for streaming metrics"


def test_monitoring_ux_has_ttft_metric_as_current_dashboard_metric() -> None:
    monitoring_ux = (ROOT / "docs/operations/monitoring_ux.md").read_text(encoding="utf-8")
    assert "streaming_time_to_first_chunk_seconds_bucket" in monitoring_ux, \
        "monitoring_ux.md must document streaming_time_to_first_chunk_seconds_bucket"


def test_dashboard_panel_datasource_uses_variable() -> None:
    for path in (ROOT / "ops/grafana/dashboards").glob("*.json"):
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for panel in iter_panels(dashboard["panels"]):
            assert panel.get("datasource") == {"type": "prometheus", "uid": "${datasource}"}, (
                f"Panel '{panel.get('title')}' in {path.name} "
                f"must use datasource {{\"type\":\"prometheus\",\"uid\":\"${{datasource}}\"}}"
            )


def test_dashboard_json_has_no_raw_prompt_or_generated_text_in_exprs() -> None:
    forbidden_label_patterns = ["prompt=", "generated_text=", "raw_input=", "user_text="]
    for path in (ROOT / "ops/grafana/dashboards").glob("*.json"):
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for panel in iter_panels(dashboard["panels"]):
            for target in panel.get("targets", []):
                expr = target.get("expr", "")
                for pattern in forbidden_label_patterns:
                    assert pattern not in expr, (
                        f"Forbidden label selector '{pattern}' found in panel "
                        f"'{panel.get('title')}' expr in {path.name}"
                    )


def test_validate_grafana_promql_script_exists() -> None:
    script = ROOT / "scripts/validation/validate_grafana_promql.py"
    assert script.exists(), "scripts/validation/validate_grafana_promql.py must exist"
    content = script.read_text(encoding="utf-8")
    assert "/api/v1/query" in content
    assert "--config-only" in content
    assert "--allow-failures" in content


def test_monitoring_config_includes_embedding_ko_vllm_port() -> None:
    """configs/monitoring.yaml must list port 9406 (embedding-ko-vllm) in vllm_instances."""
    monitoring = yaml.safe_load((ROOT / "configs/monitoring.yaml").read_text(encoding="utf-8"))
    ports = monitoring["metric_sources"]["vllm_instances"]["ports"]
    assert 9406 in ports, (
        "configs/monitoring.yaml vllm_instances.ports must include 9406 (embedding-ko-vllm)"
    )
