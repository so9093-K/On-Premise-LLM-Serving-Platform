#!/usr/bin/env python3
"""validate_grafana_promql.py — optional runtime PromQL syntax checker.

Extracts PromQL expressions from ops/grafana/dashboards/*.json,
substitutes variable defaults, and checks syntax via Prometheus /api/v1/query.

This is NOT a required CI gate. Run it against a live Prometheus instance
for optional validation only.

Usage:
    python3 scripts/validation/validate_grafana_promql.py \\
        --prometheus-url http://localhost:9410 \\
        [--allow-no-data] \\
        [--allow-failures] \\
        [--dashboards-dir ops/grafana/dashboards]

Exit codes:
    0  all expressions OK (or all failures suppressed by --allow-failures)
    1  hard errors present (syntax errors, JSON parse failures)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

VARIABLE_DEFAULTS = {
    "datasource": "prometheus",
    "window": "5m",
    "model": "local-main",
    "runtime_service": "main-llm-vllm",
    "route": "/v1/chat/completions",
    "status_code": "200",
}


def default_prometheus_url() -> str:
    """services.yaml의 Prometheus host 기본 포트를 optional 검사에도 적용한다."""
    try:
        document = yaml.safe_load((ROOT / "configs/services.yaml").read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise TypeError("services.yaml root is not a mapping")
        services = document["services"]
        port = int(services["prometheus"]["default_host_port"])
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise RuntimeError(
            "configs/services.yaml must define services.prometheus.default_host_port"
        ) from exc
    return f"http://localhost:{port}"


def substitute_variables(expr: str) -> str:
    for var, value in VARIABLE_DEFAULTS.items():
        expr = expr.replace(f"${var}", value)
        expr = expr.replace(f"${{{var}}}", value)
    return expr


def iter_panels(panels: list[dict]) -> object:
    """Recursively yield all panels, including nested panels under row panels."""
    for panel in panels:
        yield panel
        yield from iter_panels(panel.get("panels", []))


def extract_expressions(dashboard: dict) -> list[tuple[str, str, str]]:
    """Return (panel_title, refId, expr) tuples for all non-empty PromQL targets."""
    results = []
    for panel in iter_panels(dashboard.get("panels", [])):
        panel_title = panel.get("title", "unknown")
        for target in panel.get("targets", []):
            expr = target.get("expr", "").strip()
            if expr:
                results.append((panel_title, target.get("refId", "?"), expr))
    return results


class QueryResult:
    """Result of a single PromQL query attempt."""

    def __init__(self, ok: bool, no_data: bool, error_msg: str) -> None:
        self.ok = ok
        self.no_data = no_data
        self.error_msg = error_msg


def check_promql(prometheus_url: str, expr: str) -> QueryResult:
    """Query Prometheus /api/v1/query and classify the result.

    Returns:
        QueryResult with:
          ok=True, no_data=False  — success with data
          ok=True,  no_data=True  — success but empty result set
          ok=False, no_data=False — HTTP error or network failure
    """
    query = substitute_variables(expr)
    # 'time' 파라미터는 생략 — Prometheus는 기본적으로 현재 서버 시간을 사용함.
    encoded = urllib.parse.urlencode({"query": query})
    url = f"{prometheus_url}/api/v1/query?{encoded}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") != "success":
                return QueryResult(ok=False, no_data=False, error_msg=data.get("error", "unknown error"))
            result = data.get("data", {}).get("result", [])
            no_data = len(result) == 0
            return QueryResult(ok=True, no_data=no_data, error_msg="")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            error_msg = json.loads(body).get("error", body)
        except Exception:
            error_msg = body
        return QueryResult(ok=False, no_data=False, error_msg=error_msg)
    except Exception as e:
        return QueryResult(ok=False, no_data=False, error_msg=str(e))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--prometheus-url",
        default=os.environ.get("PROMETHEUS_BASE_URL", default_prometheus_url()),
        help="Prometheus base URL (default: $PROMETHEUS_BASE_URL or services.yaml host port)",
    )
    parser.add_argument(
        "--dashboards-dir",
        default="ops/grafana/dashboards",
        help="Path to Grafana dashboard JSON directory (relative to project root)",
    )
    parser.add_argument(
        "--allow-no-data",
        action="store_true",
        help=(
            "Treat empty Prometheus result sets as warnings instead of errors. "
            "Without this flag, a query that succeeds syntactically but returns no data "
            "is counted as an error."
        ),
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="Validate dashboard JSON structure only; skip all Prometheus queries.",
    )
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help=(
            "Suppress all query failures (connection errors, syntax errors, no-data) "
            "and exit 0. Final summary clearly shows warning count — "
            "does NOT print 'All expressions passed' when failures were suppressed."
        ),
    )
    args = parser.parse_args()

    dashboards_dir = ROOT / args.dashboards_dir
    if not dashboards_dir.exists():
        print(f"ERROR: dashboards directory not found: {dashboards_dir}", file=sys.stderr)
        return 1

    dashboard_paths = list(dashboards_dir.glob("*.json"))
    if not dashboard_paths:
        print(f"ERROR: no JSON files found in {dashboards_dir}", file=sys.stderr)
        return 1

    hard_errors: list[str] = []
    warnings: list[str] = []
    total = 0

    for path in sorted(dashboard_paths):
        try:
            dashboard = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            hard_errors.append(f"[{path.name}] invalid JSON: {e}")
            continue

        uid = dashboard.get("uid", "")
        if not uid:
            hard_errors.append(f"[{path.name}] missing uid field")

        expressions = extract_expressions(dashboard)
        print(f"[{path.name}] {len(expressions)} expressions")

        if args.config_only:
            continue

        for panel_title, ref_id, expr in expressions:
            total += 1
            result = check_promql(args.prometheus_url, expr)

            if not result.ok:
                msg = f"  FAIL  [{panel_title}/{ref_id}] {expr[:80]} => {result.error_msg}"
                if args.allow_failures:
                    warnings.append(msg)
                else:
                    hard_errors.append(msg)
            elif result.no_data:
                msg = f"  NODATA [{panel_title}/{ref_id}] {expr[:80]}"
                if args.allow_no_data or args.allow_failures:
                    warnings.append(msg)
                else:
                    hard_errors.append(msg)
            else:
                print(f"  OK    [{panel_title}/{ref_id}]")

    if warnings:
        print(f"\nWARNINGS ({len(warnings)}):")
        for w in warnings:
            print(w)

    if hard_errors:
        print(f"\nERRORS ({len(hard_errors)}):")
        for e in hard_errors:
            print(e)
        return 1

    if args.config_only:
        print(f"\nConfig-only check passed for {len(dashboard_paths)} dashboards.")
        return 0

    if warnings:
        print(
            f"\nPromQL validation completed with warnings suppressed.\n"
            f"Checked {total} expressions: 0 hard errors, {len(warnings)} warnings."
        )
    else:
        print(f"\nAll {total} PromQL expressions passed (no hard errors, no warnings).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
