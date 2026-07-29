"""Unit tests for scripts/validation/validate_grafana_promql.py."""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validation/validate_grafana_promql.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_grafana_promql", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vgp = _load_module()


# ---------------------------------------------------------------------------
# iter_panels — nested traversal
# ---------------------------------------------------------------------------

def test_iter_panels_flat():
    panels = [{"title": "A"}, {"title": "B"}]
    titles = [p["title"] for p in vgp.iter_panels(panels)]
    assert titles == ["A", "B"]


def test_iter_panels_nested():
    panels = [
        {"title": "Row", "panels": [{"title": "Child1"}, {"title": "Child2"}]},
        {"title": "Top"},
    ]
    titles = [p["title"] for p in vgp.iter_panels(panels)]
    assert "Row" in titles
    assert "Child1" in titles
    assert "Child2" in titles
    assert "Top" in titles


def test_extract_expressions_nested_panel():
    dashboard = {
        "panels": [
            {
                "title": "Row",
                "type": "row",
                "targets": [],
                "panels": [
                    {
                        "title": "Nested",
                        "targets": [{"expr": "up", "refId": "A"}],
                    }
                ],
            }
        ]
    }
    exprs = vgp.extract_expressions(dashboard)
    assert len(exprs) == 1
    assert exprs[0][0] == "Nested"
    assert exprs[0][2] == "up"


def test_extract_expressions_skips_empty_expr():
    dashboard = {
        "panels": [{"title": "P", "targets": [{"expr": "", "refId": "A"}, {"expr": "up", "refId": "B"}]}]
    }
    exprs = vgp.extract_expressions(dashboard)
    assert len(exprs) == 1
    assert exprs[0][2] == "up"


# ---------------------------------------------------------------------------
# substitute_variables
# ---------------------------------------------------------------------------

def test_substitute_variables_dollar_brace():
    expr = 'rate(http_requests_total[$window])'
    result = vgp.substitute_variables(expr)
    assert "$window" not in result
    assert "5m" in result


def test_substitute_variables_dollar_plain():
    expr = 'vllm_queue_depth{model=~"$model"}'
    result = vgp.substitute_variables(expr)
    assert "$model" not in result
    assert "local-main" in result


# ---------------------------------------------------------------------------
# check_promql — Prometheus response classification
# ---------------------------------------------------------------------------

def _mock_response(body: dict, status: int = 200):
    raw = json.dumps(body).encode()
    resp = MagicMock()
    resp.read.return_value = raw
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_check_promql_success_with_data():
    body = {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [1, "1"]}]}}
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        r = vgp.check_promql("http://prom", "up")
    assert r.ok is True
    assert r.no_data is False
    assert r.error_msg == ""


def test_check_promql_success_empty_result_is_no_data():
    body = {"status": "success", "data": {"resultType": "vector", "result": []}}
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        r = vgp.check_promql("http://prom", "nonexistent_metric")
    assert r.ok is True
    assert r.no_data is True


def test_check_promql_prometheus_error_status():
    body = {"status": "error", "errorType": "bad_data", "error": "parse error"}
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        r = vgp.check_promql("http://prom", "bad[expr")
    assert r.ok is False
    assert r.no_data is False
    assert "parse error" in r.error_msg


def test_check_promql_network_failure():
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("refused")):
        r = vgp.check_promql("http://prom", "up")
    assert r.ok is False
    assert r.no_data is False


# ---------------------------------------------------------------------------
# URL must not contain time=now
# ---------------------------------------------------------------------------

def test_check_promql_url_has_no_time_now():
    captured_urls = []
    body = {"status": "success", "data": {"resultType": "vector", "result": []}}

    def fake_urlopen(url, timeout=None):
        captured_urls.append(url)
        return _mock_response(body)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        vgp.check_promql("http://prom", "up")

    assert captured_urls, "urlopen was not called"
    assert "time=now" not in captured_urls[0], f"URL must not contain time=now: {captured_urls[0]}"
    assert "time=" not in captured_urls[0], f"URL must not contain time= parameter: {captured_urls[0]}"


# ---------------------------------------------------------------------------
# main() — allow-no-data flag
# ---------------------------------------------------------------------------

def _run_main(argv: list[str], dashboard_dir: Path | None = None) -> tuple[int, str]:
    """Run main() with patched sys.argv and capture stdout."""
    import io

    if dashboard_dir is None:
        dashboard_dir = ROOT / "ops/grafana/dashboards"

    full_argv = ["validate_grafana_promql.py", "--dashboards-dir", str(dashboard_dir)] + argv
    with patch("sys.argv", full_argv):
        import io as _io
        from unittest.mock import patch as _patch
        buf = _io.StringIO()
        with _patch("sys.stdout", buf):
            exit_code = vgp.main()
    return exit_code, buf.getvalue()


def test_config_only_exits_zero():
    assert vgp.default_prometheus_url() == "http://localhost:9410"
    code, out = _run_main(["--config-only"])
    assert code == 0
    assert "Config-only check passed" in out


def test_allow_no_data_treats_empty_as_warning(tmp_path):
    dashboard = {
        "uid": "test_dash",
        "title": "Test",
        "tags": ["contract-reference"],
        "panels": [{"title": "P", "type": "stat", "datasource": {"type": "prometheus", "uid": "${datasource}"}, "targets": [{"expr": "nonexistent_metric", "refId": "A"}], "description": "test"}],
        "templating": {"list": []},
    }
    (tmp_path / "test.json").write_text(json.dumps(dashboard))
    body = {"status": "success", "data": {"resultType": "vector", "result": []}}
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        code, out = _run_main(["--allow-no-data", "--prometheus-url", "http://prom"], dashboard_dir=tmp_path)
    assert code == 0
    assert "NODATA" in out or "warnings" in out.lower()


def test_no_allow_no_data_treats_empty_as_error(tmp_path):
    dashboard = {
        "uid": "test_dash",
        "title": "Test",
        "tags": [],
        "panels": [{"title": "P", "type": "stat", "datasource": {"type": "prometheus", "uid": "${datasource}"}, "targets": [{"expr": "nonexistent_metric", "refId": "A"}], "description": "test"}],
        "templating": {"list": []},
    }
    (tmp_path / "test.json").write_text(json.dumps(dashboard))
    body = {"status": "success", "data": {"resultType": "vector", "result": []}}
    with patch("urllib.request.urlopen", return_value=_mock_response(body)):
        code, out = _run_main(["--prometheus-url", "http://prom"], dashboard_dir=tmp_path)
    assert code == 1
    assert "NODATA" in out or "ERROR" in out


def test_allow_failures_does_not_print_all_passed_on_failures(tmp_path):
    """--allow-failures must not claim 'All expressions passed' when failures occurred."""
    dashboard = {
        "uid": "test_dash",
        "title": "Test",
        "tags": [],
        "panels": [{"title": "P", "type": "stat", "datasource": {"type": "prometheus", "uid": "${datasource}"}, "targets": [{"expr": "up", "refId": "A"}], "description": "test"}],
        "templating": {"list": []},
    }
    (tmp_path / "test.json").write_text(json.dumps(dashboard))

    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("refused")):
        code, out = _run_main(["--allow-failures", "--prometheus-url", "http://prom"], dashboard_dir=tmp_path)

    assert code == 0, "allow-failures must exit 0 even on connection failure"
    assert "All" not in out or "passed" not in out or "warnings" in out.lower(), (
        "allow-failures must not print 'All expressions passed' when there were failures"
    )
    assert "warnings" in out.lower() or "WARNINGS" in out or "warning" in out.lower(), (
        "allow-failures must mention warnings in output"
    )
