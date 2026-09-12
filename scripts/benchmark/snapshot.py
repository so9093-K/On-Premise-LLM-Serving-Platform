"""측정 구간의 runtime·infrastructure 상태를 Prometheus에서 읽는다(ADR-0026 Epic 4).

판정 근거가 아니라 해석 근거다. percentile은 원시 샘플에서 계산하고, 여기서 읽는
값은 "그때 런타임이 어떤 상태였는가"를 설명한다. 계약이 그 둘을 layer와 role로
갈라 두었으므로 여기서 다시 정하지 않는다.

무엇을 어떤 이름으로 읽을지는 계약이 소유한다. unsupported로 선언된 조합은
질의하지 않는다 -- 없는 series를 물어 빈 값을 받으면 "0이었다"와 구분되지 않는다.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx

from scripts.benchmark.contract import ROOT, PerformanceContract, load_yaml_mapping
from scripts.lib.service_endpoint import published_base_url

# aggregation_unit별로 묻는 것이 다르다. 같은 질의로 뭉뚱그리면 counter의 증가분과
# gauge의 현재값이 같은 칸에 들어간다.
_INSTANT = "instant"
_COUNTER = "counter"
_REQUEST = "request"

_MONITORING_PATH = ROOT / "configs" / "monitoring.yaml"


def scrape_interval_seconds() -> float:
    """Prometheus가 실제로 긁는 간격. 이 값이 스냅샷의 해상도를 정한다.

    설정 파일이 소유한다. 여기서 15초를 다시 적으면 운영자가 간격을 바꿨을 때
    스냅샷만 옛 가정으로 계산한다.
    """
    stack = (load_yaml_mapping(_MONITORING_PATH).get("monitoring_stack") or {})
    raw = str((stack.get("prometheus") or {}).get("scrape_interval", "15s")).strip()
    return float(raw[:-1]) if raw.endswith("s") else float(raw)


def prometheus_base(override: str = "") -> str:
    """Prometheus 주소. runtime validation과 같은 곳에서 해석한다."""
    if override.strip():
        return override.strip()
    env = os.getenv("RUNTIME_VALIDATION_PROMETHEUS_BASE_URL", "").strip()
    if env:
        return env
    services = load_yaml_mapping(ROOT / "configs" / "services.yaml")["services"]
    return published_base_url(services, "prometheus")


class SnapshotUnavailable(RuntimeError):
    """수집 자체를 하지 못한 상태. 값이 0인 것과 다르다."""


def _sources(metric: dict[str, Any], environment: dict[str, Any], marker: str) -> str | None:
    """이 실행 환경에서 이 지표를 Prometheus의 어떤 이름으로 읽는가.

    gateway 층은 target과 무관하게 같은 코드가 만들므로 이름이 하나다. 다만 그
    값이 Prometheus에 있는 것과 접근 로그에만 있는 것이 섞여 있어, 어느 쪽인지는
    계약의 source_kind가 말한다. 이름 모양으로 추측하면 한쪽을 조용히 건너뛴다.
    """
    if metric["layer"] == "gateway":
        return str(metric["source"]) if metric.get("source_kind") == "prometheus" else None
    if metric["layer"] == "runtime":
        key = str(environment.get("runtime_backend", ""))
        table = metric.get("backends") or {}
    else:
        key = str(environment.get("deployment_target", ""))
        table = metric.get("targets") or {}
    source = table.get(key)
    if source is None or source == marker:
        return None
    return str(source)


class PrometheusReader:
    """Prometheus HTTP API. 실패를 삼키지 않는다."""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout

    def range_values(self, query: str, start: float, end: float, step: float) -> list[float]:
        params = {"query": query, "start": f"{start:.3f}", "end": f"{end:.3f}", "step": f"{step:g}"}
        try:
            response = httpx.get(
                f"{self.base_url}/api/v1/query_range", params=params, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise SnapshotUnavailable(f"cannot reach Prometheus at {self.base_url}: {exc}") from exc
        if response.status_code != 200:
            raise SnapshotUnavailable(
                f"Prometheus returned {response.status_code} for {query!r}"
            )
        payload = response.json()
        if payload.get("status") != "success":
            raise SnapshotUnavailable(f"Prometheus rejected {query!r}: {payload.get('error')}")
        values: list[float] = []
        for series in payload.get("data", {}).get("result", []):
            for _, raw in series.get("values", []):
                try:
                    number = float(raw)
                except (TypeError, ValueError):
                    continue
                if number == number:  # NaN은 값이 아니다
                    values.append(number)
        return values


def _entry(values: list[float], unit: str) -> dict[str, Any] | None:
    if not values:
        return None
    distinct = len({round(value, 9) for value in values})
    if unit == _COUNTER:
        # counter는 구간의 시작과 끝만 의미가 있다. 평균은 누적값의 평균이라 뜻이 없다.
        # 서로 다른 값이 하나면 구간 안에 scrape가 한 번뿐이라 증가분을 알 수 없다.
        if distinct < 2:
            return None
        return {"start": values[0], "end": values[-1], "samples": distinct}
    return {
        "start": values[0],
        "end": values[-1],
        "avg": sum(values) / len(values),
        "max": max(values),
        "samples": distinct,
    }


def collect(
    contract: PerformanceContract,
    document: dict[str, Any],
    reader: PrometheusReader,
    *,
    step_seconds: float = 5.0,
) -> dict[str, Any]:
    """이 실행 구간에 해당하는 런타임 상태를 읽는다.

    시간 구간은 결과 문서가 이미 갖고 있다. 따로 받으면 측정과 스냅샷이 서로 다른
    구간을 볼 수 있다.
    """
    started, ended = _window(document)
    # counter는 측정 구간 안에서만 읽으면 체계적으로 과소 집계된다. 요청이 구간
    # 안에서 끝나도 그 증가분은 다음 scrape에 잡히고, 그 scrape는 구간 밖이다.
    # 실측에서 4건이 1,024 토큰을 생성했는데 구간 안 delta는 2였다. 앞뒤로 scrape
    # 간격만큼 넓혀, 구간 직전 값과 구간 직후 값으로 감싼다.
    interval = scrape_interval_seconds()
    query_start, query_end = started - interval, ended + interval
    environment = document.get("environment") or {}
    marker = contract.unsupported_marker
    snapshot: dict[str, Any] = {}
    for name, metric in contract.metrics.items():
        if metric["layer"] not in ("gateway", "runtime", "infrastructure"):
            continue
        source = _sources(metric, environment, marker)
        if source is None:
            # 계약이 이 조합에서 측정 불가라고 선언했다. 없는 series를 물어 빈 값을
            # 받으면 "0이었다"와 구분되지 않는다.
            continue
        unit = str(metric.get("aggregation_unit", _INSTANT))
        query = _query(source, unit, started, ended)
        values = reader.range_values(query, query_start, query_end, step_seconds)
        entry = _entry(values, unit)
        if entry is not None:
            snapshot[name] = entry
    return snapshot


def _query(source: str, unit: str, started: float, ended: float) -> str:
    if unit == _REQUEST:
        # histogram은 bucket에서 분위수를 뽑는다. 단, 판정에는 쓰지 않는다 --
        # bucket 경계가 값을 결정하기 때문이다(ADR-0026 9절).
        window = max(1, int(ended - started))
        return f"histogram_quantile(0.95, sum by (le) (rate({source}_bucket[{window}s])))"
    return source


def _window(document: dict[str, Any]) -> tuple[float, float]:
    run = document["run"]
    started_at = str(run["started_at"]).replace("Z", "+00:00")
    start = datetime.fromisoformat(started_at).timestamp()
    return start, start + float(run["duration_seconds"])
