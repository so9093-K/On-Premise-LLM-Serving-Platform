from __future__ import annotations

from collections.abc import Iterator

from .helpers import *  # noqa: F401,F403

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


def test_operator_grafana_status_board_is_user_facing() -> None:
    monitoring = yaml.safe_load((ROOT / "configs/monitoring.yaml").read_text(encoding="utf-8"))
    operator = monitoring["operator_status_ux"]
    assert operator["landing_dashboard"] == "gpu_capacity_and_oom_risk"
    assert operator["drill_down_order"][0] == "serving_home"
    assert "gpu_headroom" in operator["first_screen_order"]
    assert {item["mode"] for item in operator["serving_modes"]} == {
        "ACTIVE",
        "IDLE WARM",
        "IDLE COLD",
        "DEGRADED",
        "NO DATA",
    }
    assert {item["level"] for item in operator["status_levels"]} == {"green", "yellow", "red", "gray"}
    status_doc = (ROOT / "docs/operations/grafana_status_board.md").read_text(encoding="utf-8")
    assert "지금 요청을 안전하게 처리할 수 있는가?" in status_doc
    home = json.loads((ROOT / "ops/grafana/dashboards/serving_home.json").read_text(encoding="utf-8"))
    home_titles = {panel["title"] for panel in home["panels"]}
    home_required = set(grafana_contract("ops/grafana/dashboards/serving_home.json")["required_panels"])
    assert home_required.issubset({panel["title"] for panel in iter_panels(home["panels"])})
    assert len(home["panels"]) <= 20
    variables_by_name = {item["name"]: item for item in home["templating"]["list"]}
    home_variables = set(variables_by_name)
    assert "user_route" in home_variables
    user_route_text = json.dumps(variables_by_name["user_route"], ensure_ascii=False)
    assert "/v1/risk/.*" in user_route_text
    assert re.search(r"(?<!/v1)/risk/\.\*", user_route_text) is None
    user_route_options = {item["text"]: item["value"] for item in variables_by_name["user_route"]["options"]}
    assert user_route_options == {
        "All user routes": "/v1/chat/completions|/v1/embeddings|/v1/risk/.*|/v1/retrieval/.*",
        "Chat": "/v1/chat/completions",
        "Embeddings": "/v1/embeddings",
        "Risk": "/v1/risk/.*",
        "Retrieval": "/v1/retrieval/.*",
    }


def test_grafana_panels_include_operator_descriptions() -> None:
    for path in [
        "ops/grafana/dashboards/serving_home.json",
        "ops/grafana/dashboards/gpu_capacity_and_oom_risk.json",
        "ops/grafana/dashboards/risk_signal_operations.json",
        "ops/grafana/dashboards/api_experience.json",
        "ops/grafana/dashboards/model_runtime_deep_dive.json",
        "ops/grafana/dashboards/observability_data_quality.json",
    ]:
        dashboard = json.loads((ROOT / path).read_text(encoding="utf-8"))
        for panel in iter_panels(dashboard["panels"]):
            description = panel.get("description", "")
            assert description
            assert any(token in description for token in ["Healthy", "Attention", "Action Required", "No Runtime Data", "No Data", "Action"])


def test_detail_panels_are_collapsed_below_snapshot_cards() -> None:
    dashboards = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / "ops/grafana/dashboards").glob("*.json")
    }

    api_rows = {panel["title"]: panel for panel in dashboards["api_experience.json"]["panels"] if panel["type"] == "row"}
    api_expected_rows = set(grafana_contract("ops/grafana/dashboards/api_experience.json")["collapsed_detail_rows"])
    assert api_expected_rows.issubset(api_rows)
    for row in api_rows.values():
        assert row.get("collapsed") is True
        assert row.get("panels"), f"{row['title']} must own the detailed panels it hides"

    api_top_titles = {panel["title"] for panel in dashboards["api_experience.json"]["panels"] if panel["type"] != "row"}
    assert "Retrieval Requests" not in api_top_titles
    assert "Embedding Response Size" not in api_top_titles
    assert "Streaming Output Rate" not in api_top_titles

    risk_rows = {panel["title"]: panel for panel in dashboards["risk_signal_operations.json"]["panels"] if panel["type"] == "row"}
    risk_expected_rows = set(grafana_contract("ops/grafana/dashboards/risk_signal_operations.json").get("collapsed_detail_rows", []))
    for title in risk_expected_rows:
        assert risk_rows[title].get("collapsed") is True
    risk_top_titles = {panel["title"] for panel in dashboards["risk_signal_operations.json"]["panels"] if panel["type"] != "row"}
    assert "Risk Types" not in risk_top_titles
    assert "System Signals" not in risk_top_titles

    obs_rows = {panel["title"]: panel for panel in dashboards["observability_data_quality.json"]["panels"] if panel["type"] == "row"}
    obs_expected_rows = set(grafana_contract("ops/grafana/dashboards/observability_data_quality.json")["collapsed_detail_rows"])
    for title in obs_expected_rows:
        assert obs_rows[title].get("collapsed") is True
    obs_top_titles = {panel["title"] for panel in dashboards["observability_data_quality.json"]["panels"] if panel["type"] != "row"}
    assert "Derived Signals" not in obs_top_titles


def test_grafana_dashboards_are_english_titled_variable_backed_and_streaming_aware() -> None:
    dashboards = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in (ROOT / "ops/grafana/dashboards").glob("*.json")}
    assert set(dashboards) >= {
        "serving_home.json",
        "gpu_capacity_and_oom_risk.json",
        "risk_signal_operations.json",
        "api_experience.json",
        "model_runtime_deep_dive.json",
        "observability_data_quality.json",
    }
    for dashboard in dashboards.values():
        assert " / " not in dashboard["title"]
        variables = {item["name"] for item in dashboard["templating"]["list"]}
        assert {"datasource", "window"}.issubset(variables)
        assert dashboard["panels"]
        for panel in dashboard["panels"]:
            if panel["type"] == "stat" and panel.get("options", {}).get("colorMode") == "background":
                assert panel["options"].get("graphMode") == "none"
            if panel["type"] == "timeseries":
                assert panel.get("options", {}).get("legend", {}).get("showLegend") is True
    api_text = json.dumps(dashboards["api_experience.json"], ensure_ascii=False)
    for metric in ["streaming_chunks_total", "streaming_bytes_total", "streaming_errors_total", "streaming_usage_events_total"]:
        assert metric in api_text
    assert 'path=~\\"chat/completions(:stream)?\\"' in api_text
    home_text = json.dumps(dashboards["serving_home.json"], ensure_ascii=False)
    assert 'route=~\\"$user_route\\"' in home_text
    assert "ai_serving_verdict_code" in home_text
    assert "backend_restart_total" not in home_text
    assert "gpu_oom_events_total" not in home_text
    assert "container_oom_events_total" in home_text
    assert "container_start_time_seconds" in home_text
    home_panels = {panel["title"]: panel for panel in dashboards["serving_home.json"]["panels"]}
    home_semantic = grafana_contract("ops/grafana/dashboards/serving_home.json")["semantic_panels"]
    assert home_semantic["attention"] in home_text
    oom_panel_text = json.dumps(home_panels[home_semantic["crashes_restarts"]], ensure_ascii=False)
    assert "or vector(0)" not in oom_panel_text
    user_traffic_text = json.dumps(home_panels[home_semantic["user_requests"]], ensure_ascii=False)
    assert 'service=\\"gateway\\"' in user_traffic_text
    assert 'status_code=~\\"$status_code\\"' not in user_traffic_text
    for dashboard_name, dashboard in dashboards.items():
        if dashboard_name != "serving_home.json":
            assert any(link.get("title") == "Serving Home" for link in dashboard.get("links", []))
        assert "Home dashboard — GPU" not in json.dumps(dashboard, ensure_ascii=False)
    gpu_text = json.dumps(dashboards["gpu_capacity_and_oom_risk.json"], ensure_ascii=False)
    assert "backend_restart_total" not in gpu_text
    assert "gpu_oom_events_total" not in gpu_text
    assert "container_oom_events_total" in gpu_text
    assert "container_start_time_seconds" in gpu_text


def test_endpoint_reference_matches_monitoring_dashboard_inventory() -> None:
    endpoint_doc = (ROOT / "docs/operations/endpoint_reference.md").read_text(encoding="utf-8")
    for dashboard_id in [
        "serving_home",
        "api_experience",
        "model_runtime_deep_dive",
        "gpu_capacity_and_oom_risk",
        "risk_signal_operations",
        "observability_data_quality",
    ]:
        assert f"`{dashboard_id}`" in endpoint_doc
    assert "clamp_min(sum(rate(http_requests_total" not in endpoint_doc
    assert "allowUiUpdates=false" in endpoint_doc


def test_dashboard_navigation_links_are_nonempty_and_uid_valid() -> None:
    dashboards_dir = ROOT / "ops/grafana/dashboards"
    dashboards = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in dashboards_dir.glob("*.json")
    }
    valid_uids = {d["uid"] for d in dashboards.values()}

    for name, dashboard in dashboards.items():
        links = dashboard.get("links", [])
        assert links, f"Dashboard '{name}' has no navigation links"
        for link in links:
            url = link.get("url", "")
            assert "/d/" in url, f"Dashboard '{name}' link has unexpected URL format: {url}"
            target_uid = url.split("/d/")[-1].split("/")[0]
            assert target_uid in valid_uids, (
                f"Dashboard '{name}' links to unknown uid '{target_uid}' (url={url})"
            )


def test_no_mixed_unit_panel_titles() -> None:
    chat = json.loads(
        (ROOT / "ops/grafana/dashboards/api_experience.json").read_text(encoding="utf-8")
    )
    model = json.loads(
        (ROOT / "ops/grafana/dashboards/model_runtime_deep_dive.json").read_text(encoding="utf-8")
    )
    chat_titles = {panel["title"] for panel in chat["panels"]}
    model_titles = {panel["title"] for panel in model["panels"]}

    assert "Streaming Chunks and Bytes" not in chat_titles, \
        "Mixed-unit panel 'Streaming Chunks and Bytes' must be split"
    assert "Streaming Duration and Chunks" not in chat_titles, \
        "Mixed-unit panel 'Streaming Duration and Chunks' must be split"
    assert "Queue and KV Trend" not in model_titles, \
        "Mixed-unit panel 'Queue and KV Trend' must be split"


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
    assert "다음 항목은 현재 metric만으로 정확히 계산하지 않는다" not in monitoring_ux, \
        "Stale 'not calculated' text must be removed — TTFT/duration/chunks are now in the dashboard"


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
