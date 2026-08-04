#!/usr/bin/env python3
"""validate_grafana_promql.py — 선택적 런타임 PromQL 구문 검사기.

ops/grafana/dashboards/*.json에서 PromQL 표현식을 추출하고, 변수 기본값을
치환한 뒤 Prometheus /api/v1/query로 구문을 확인한다.

이건 필수 CI gate가 아니다. 실제 Prometheus 인스턴스가 있어야 하므로 선택적
검증 용도로만 돌린다.

사용법:
    python3 scripts/validation/validate_grafana_promql.py \\
        --prometheus-url http://localhost:9410 \\
        [--allow-no-data] \\
        [--allow-failures] \\
        [--dashboards-dir ops/grafana/dashboards]

종료 코드:
    0  모든 표현식 OK (또는 --allow-failures로 모든 실패가 억제됨)
    1  치명적 오류 존재 (구문 오류, JSON 파싱 실패)
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
    """row panel 아래 중첩된 panel까지 포함해 모든 panel을 재귀적으로 순회한다."""
    for panel in panels:
        yield panel
        yield from iter_panels(panel.get("panels", []))


def extract_expressions(dashboard: dict) -> list[tuple[str, str, str]]:
    """비어있지 않은 모든 PromQL target에 대해 (panel_title, refId, expr) 튜플을 반환한다."""
    results = []
    for panel in iter_panels(dashboard.get("panels", [])):
        panel_title = panel.get("title", "unknown")
        for target in panel.get("targets", []):
            expr = target.get("expr", "").strip()
            if expr:
                results.append((panel_title, target.get("refId", "?"), expr))
    return results


class QueryResult:
    """PromQL 쿼리 한 번 시도한 결과."""

    def __init__(self, ok: bool, no_data: bool, error_msg: str) -> None:
        self.ok = ok
        self.no_data = no_data
        self.error_msg = error_msg


def check_promql(prometheus_url: str, expr: str) -> QueryResult:
    """Prometheus /api/v1/query에 질의하고 결과를 분류한다.

    반환값:
        QueryResult로:
          ok=True, no_data=False  — 성공, 데이터 있음
          ok=True, no_data=True   — 성공했지만 결과셋이 비어있음
          ok=False, no_data=False — HTTP 오류 또는 네트워크 실패
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
        help="Prometheus base URL (기본값: $PROMETHEUS_BASE_URL 또는 services.yaml host port)",
    )
    parser.add_argument(
        "--dashboards-dir",
        default="ops/grafana/dashboards",
        help="Grafana dashboard JSON 디렉터리 경로 (project root 기준 상대경로)",
    )
    parser.add_argument(
        "--allow-no-data",
        action="store_true",
        help=(
            "빈 Prometheus 결과셋을 오류가 아니라 경고로 취급합니다. "
            "이 플래그가 없으면, 구문은 성공했지만 데이터가 없는 쿼리도 "
            "오류로 집계됩니다."
        ),
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="dashboard JSON 구조만 검증하고 Prometheus 쿼리는 전부 건너뜁니다.",
    )
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help=(
            "모든 쿼리 실패(연결 오류, 구문 오류, no-data)를 억제하고 exit 0으로 "
            "종료합니다. 최종 요약에는 경고 개수가 명확히 표시됩니다 — "
            "실패가 억제된 경우 'All expressions passed'라고 출력하지 않습니다."
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
