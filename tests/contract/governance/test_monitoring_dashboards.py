from __future__ import annotations

from collections.abc import Iterator

from .helpers import *  # noqa: F401,F403

GPU_DASHBOARD = "ops/grafana/dashboards/gpu_capacity_and_oom_risk.json"
USAGE_DASHBOARD = "ops/grafana/dashboards/usage_today.json"


def iter_panels(panels: list[dict]) -> Iterator[dict]:
    for panel in panels:
        yield panel
        yield from iter_panels(panel.get("panels", []))


LOG_DASHBOARD = "ops/grafana/dashboards/request_log_explorer.json"


def test_monitoring_dashboards_are_the_curated_boards() -> None:
    # gpu_capacity_and_oom_risk / usage_today는 Prometheus 메트릭 기반이라
    # configs/monitoring.yaml의 dashboard_contracts(required_panels)로 관리된다.
    # request_log_explorer는 Loki 로그 기반이라 "패널에 대응하는 메트릭이 있는가"라는
    # 이 계약의 전제 자체가 안 맞아 그 계약 시스템에는 안 넣는다 — 대신 파일
    # 존재 여부만 이 테스트에서 같이 확인한다.
    dashboards = {path.name for path in (ROOT / "ops/grafana/dashboards").glob("*.json")}
    assert dashboards == {
        "gpu_capacity_and_oom_risk.json",
        "usage_today.json",
        "request_log_explorer.json",
    }
    monitoring = yaml.safe_load((ROOT / "configs/monitoring.yaml").read_text(encoding="utf-8"))
    assert "operator_status_ux" not in monitoring
    contracts = monitoring["monitoring_stack"]["grafana"]["dashboard_contracts"]
    assert {contract["path"] for contract in contracts} == {GPU_DASHBOARD, USAGE_DASHBOARD}
    ux_ids = {item["id"] for item in monitoring["ux_dashboards"]}
    assert ux_ids == {"gpu_capacity_and_oom_risk", "usage_today"}


def test_log_dashboard_is_loki_backed_and_links_to_usage_today() -> None:
    dashboard = json.loads((ROOT / LOG_DASHBOARD).read_text(encoding="utf-8"))
    assert dashboard["uid"] == "request_log_explorer"
    variables = {item["name"] for item in dashboard["templating"]["list"]}
    assert {"datasource"}.issubset(variables)
    # container_id로 좁히는 건 전용 template 변수가 아니라 Loki 기본 ad-hoc filter(로그 줄의
    # container_id 필드 옆 "+" 아이콘)로 한다 -- 전용 변수를 따로 두면 같은 라벨에 서로 다른
    # 값을 요구하는 ad-hoc filter와 충돌해서 Raw Container Log가 아무것도 안 보였다.
    # request_log_explorer.json의 Raw Container Log 패널 description 참고.
    for panel in iter_panels(dashboard["panels"]):
        assert panel.get("datasource", {}).get("type") == "loki", (
            f"Panel '{panel.get('title')}' in {LOG_DASHBOARD} must use the Loki datasource"
        )
        assert panel.get("description"), f"Panel '{panel.get('title')}' in {LOG_DASHBOARD} needs a description"
    links = {link["url"] for link in dashboard.get("links", [])}
    assert any("/d/usage_today" in url for url in links)


def test_gpu_dashboard_is_variable_backed_and_uses_typed_panel_options() -> None:
    # uid/title/variables/필수 패널/패널 description/backend_restart_total 등
    # 폐기 metric 부재는 governance_validation.monitoring_dashboards
    # .validate_grafana_dashboard_templates()(make validate)가 이미 configs/monitoring.yaml
    # dashboard_contracts 기준으로 GPU/USAGE 대시보드 전체를 검증한다. 여기서는 거기서
    # 다루지 않는 stat/timeseries 패널 옵션(colorMode/graphMode/legend)만 확인한다.
    dashboard = json.loads((ROOT / GPU_DASHBOARD).read_text(encoding="utf-8"))
    for panel in dashboard["panels"]:
        if panel["type"] == "stat" and panel.get("options", {}).get("colorMode") == "background":
            assert panel["options"].get("graphMode") == "none"
        if panel["type"] == "timeseries":
            assert panel.get("options", {}).get("legend", {}).get("showLegend") is True


def test_dashboard_navigation_links_resolve_to_existing_uids() -> None:
    dashboards = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / "ops/grafana/dashboards").glob("*.json")
    }
    valid_uids = {d["uid"] for d in dashboards.values()}
    for name, dashboard in dashboards.items():
        for link in dashboard.get("links", []):
            url = link.get("url", "")
            if url.startswith("/a/"):
                # Grafana app plugin page (e.g. the bundled Logs Drilldown app), not
                # another dashboard -- no dashboard uid to resolve against.
                continue
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


# streaming_time_to_first_chunk_seconds_bucket가 monitoring_ux.md에 있는지는
# governance_validation.docs_ops.validate_monitoring_reference()(make validate)가
# 이미 검증한다.


def test_log_dashboard_panels_use_loki_datasource_variable() -> None:
    # GPU/USAGE(Prometheus) 대시보드의 패널 datasource 변수 사용은
    # governance_validation.monitoring_dashboards
    # .validate_grafana_dashboard_templates()(make validate)가 검증한다. 여기서는
    # request_log_explorer(Loki)만 확인한다 -- datasource.type=="loki"인 것은
    # test_log_dashboard_is_loki_backed_and_links_to_usage_today가 이미 보지만,
    # uid가 하드코딩이 아니라 "${datasource}" 변수를 쓰는지는 여기서만 확인한다.
    dashboard = json.loads((ROOT / LOG_DASHBOARD).read_text(encoding="utf-8"))
    for panel in iter_panels(dashboard["panels"]):
        assert panel.get("datasource") == {"type": "loki", "uid": "${datasource}"}, (
            f"Panel '{panel.get('title')}' in {LOG_DASHBOARD} "
            'must use datasource {"type":"loki","uid":"${datasource}"}'
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
