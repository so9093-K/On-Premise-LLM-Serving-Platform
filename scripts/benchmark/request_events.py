"""Gateway 접근 로그에서 요청별 admission 대기 시간을 가져온다(ADR-0026 Epic 4).

Gateway는 이 값을 metric으로 내보내지 않는다. 응답 헤더로 내보내면 내부 타이밍이
공개 API가 되므로 접근 로그에만 남긴다. 계약이 그 사실을 source_kind: access_log로
선언하고, 여기서 client가 이미 기록한 request_id로 이어 붙인다.

Prometheus 스냅샷과 다른 점은 집계가 아니라 요청 단위라는 것이다. 그래서 이 값은
요청 샘플에 들어가고, percentile을 원시 샘플에서 계산하는 규칙이 그대로 적용된다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from scripts.benchmark.contract import ROOT

# 호스트에서 이 디렉터리가 어디인지는 scripts/lib/gateway_runtime_state.sh가
# 선언한다. 경로를 여기 다시 적으면 배치를 바꿀 때 한쪽만 고치게 된다.
_PATH_DECLARATION = ROOT / "scripts" / "lib" / "gateway_runtime_state.sh"
_PATH_VARIABLE = "REQUEST_EVENT_LOG_DIR_RELPATH"
# Gateway가 서비스 이름으로 파일을 만든다(service_logging.py).
_GATEWAY_LOG_STEM = "gateway"
# 로그는 회전한다. gateway.jsonl.1 같은 파일에 측정 구간의 앞부분이 남아 있을 수 있다.
_ROTATION_GLOB = f"{_GATEWAY_LOG_STEM}.jsonl*"


class RequestEventsUnavailable(RuntimeError):
    """조인할 로그를 찾지 못한 상태. 대기 시간이 0이었던 것과 다르다."""


def log_directory() -> Path:
    """접근 로그가 놓인 호스트 디렉터리."""
    for line in _PATH_DECLARATION.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == _PATH_VARIABLE:
            return ROOT / value.strip().strip('"').strip("'")
    raise RequestEventsUnavailable(
        f"{_PATH_DECLARATION.name} does not declare {_PATH_VARIABLE}"
    )


def admission_waits(directory: Path | None = None) -> dict[str, float]:
    """request_id별 admission 대기 시간(초).

    로그는 이 벤치마크만 쓰는 파일이 아니다. 다른 실행의 줄도 섞여 있으므로
    request_id로만 고르고, 해당 필드가 없는 줄은 건너뛴다.
    """
    target = directory or log_directory()
    files = sorted(target.glob(_ROTATION_GLOB)) if target.is_dir() else []
    if not files:
        raise RequestEventsUnavailable(
            f"no {_ROTATION_GLOB} under {target}; Gateway가 접근 로그를 쓰고 있는지 확인한다"
        )
    waits: dict[str, float] = {}
    for path in files:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "queue_wait_ms" not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = record.get("request_id")
            value = record.get("queue_wait_ms")
            if isinstance(request_id, str) and isinstance(value, (int, float)):
                # 계약은 초 단위를 쓴다(Prometheus base unit 규약). 로그는 ms다.
                waits[request_id] = float(value) / 1000.0
    return waits


def attach_with_retry(
    document: dict[str, Any],
    *,
    directory: Path | None = None,
    timeout_seconds: float = 3.0,
    interval_seconds: float = 0.25,
) -> dict[str, Any]:
    """로그가 따라올 때까지 잠깐 기다렸다 잇는다.

    Gateway는 요청이 끝난 뒤에 한 줄을 쓴다. 마지막 요청이 끝나자마자 읽으면 그
    줄이 아직 없다 -- 실제로 4건 중 3건만 이어졌다. 데이터가 없는 것이 아니라
    우리가 이르게 읽은 것이므로, 전부 이어질 때까지 짧게 기다린다.
    """
    wanted = {
        sample["request_id"] for sample in document["requests"]
        if isinstance(sample.get("request_id"), str)
    }
    deadline = time.monotonic() + timeout_seconds
    waits: dict[str, float] = {}
    while True:
        waits = admission_waits(directory)
        if not wanted - set(waits) or time.monotonic() >= deadline:
            break
        time.sleep(interval_seconds)
    return attach(document, waits)


def attach(document: dict[str, Any], waits: dict[str, float]) -> dict[str, Any]:
    """요청 샘플에 대기 시간을 붙이고, 몇 건이 이어졌는지 돌려준다.

    전부 이어지지 않을 수 있다. 로그가 회전해 앞부분이 사라졌거나 아직 flush되지
    않았을 수 있다. 조용히 일부만 채우면 그 부분집합으로 계산한 값이 전체를
    대표하는 것처럼 읽히므로, 붙은 건수를 결과에 남긴다.
    """
    matched = 0
    requests = []
    for sample in document["requests"]:
        request_id = sample.get("request_id")
        wait = waits.get(request_id) if isinstance(request_id, str) else None
        if wait is None:
            requests.append(sample)
            continue
        requests.append({**sample, "gateway_admission_wait_seconds": wait})
        matched += 1
    joined = {**document, "requests": requests}
    joined["run"] = {**document["run"], "admission_wait_joined_requests": matched}
    return joined
