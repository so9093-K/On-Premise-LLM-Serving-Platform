from __future__ import annotations

import base64
import json
import time
import urllib.request
from typing import Any

from .config import RuntimeValidationConfig


# SSE stream을 끝(`[DONE]`)까지 볼 수 있어야 종료 계약을 확인할 수 있다.
# 이 제한은 제품 설정이 아니라 canary HTTP client의 메모리 안전장치다. 라인을
# 중간에 잘라 JSON을 추측하지 않고, 전체 이벤트를 유지하되 총 바이트를 제한한다.
_MAX_STREAM_LINES = 512
_MAX_STREAM_BYTES = 1024 * 1024


class RuntimeValidationHttpClient:
    """live validation 검사에서 공통으로 쓰는 작은 HTTP helper다.

    The validator keeps orchestration/reporting responsibility while this class
    owns auth header selection, JSON request encoding, plain text scrapes, and
    latency measurement.  It intentionally avoids request/response logging so
    runtime reports never capture prompt or model output text.
    """

    def __init__(self, config: RuntimeValidationConfig) -> None:
        self.config = config

    def headers(self, *, internal: bool = False, admin: bool = False, grafana: bool = False) -> dict[str, str]:
        values = {"Content-Type": "application/json"}
        if grafana:
            credentials = f"{self.config.grafana_admin_user}:{self.config.grafana_admin_password}".encode("utf-8")
            values["Authorization"] = "Basic " + base64.b64encode(credentials).decode("ascii")
            return values
        token = (
            self.config.admin_api_key
            if admin
            else (self.config.internal_service_token if internal else self.config.api_key)
        )
        if token:
            values["Authorization"] = f"Bearer {token}"
        return values

    def json(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        internal: bool = False,
        admin: bool = False,
        grafana: bool = False,
    ) -> tuple[int, dict[str, Any], int]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers=self.headers(internal=internal, admin=admin, grafana=grafana),
        )
        start = time.monotonic()
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            body = response.read().decode("utf-8")
            elapsed = int((time.monotonic() - start) * 1000)
            return response.status, json.loads(body), elapsed

    def text(self, url: str, *, internal: bool = False, admin: bool = False, grafana: bool = False) -> tuple[int, str, int]:
        request = urllib.request.Request(
            url,
            method="GET",
            headers=self.headers(internal=internal, admin=admin, grafana=grafana),
        )
        start = time.monotonic()
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            body = response.read().decode("utf-8")
            elapsed = int((time.monotonic() - start) * 1000)
            return response.status, body, elapsed

    def streaming_lines(
        self,
        method: str,
        url: str,
        payload: dict[str, Any],
        *,
        internal: bool = False,
        admin: bool = False,
        grafana: bool = False,
    ) -> tuple[int, str, int, list[str], bool]:
        """크기가 제한된 SSE stream을 읽고 첫 chunk 도착 지연 시간을 반환한다.

        The returned event lines are bounded in memory but remain complete JSON
        events so callers do not infer protocol state from truncated text. The
        validator does not persist token deltas or generated content.
        """
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method=method,
            headers=self.headers(internal=internal, admin=admin, grafana=grafana),
        )
        started = time.monotonic()
        first_chunk_ms: int | None = None
        lines: list[str] = []
        stream_bytes = 0
        saw_done = False
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            content_type = response.headers.get("content-type", "")
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if first_chunk_ms is None:
                    first_chunk_ms = int((time.monotonic() - started) * 1000)
                stream_bytes += len(raw_line)
                if stream_bytes > _MAX_STREAM_BYTES or len(lines) >= _MAX_STREAM_LINES:
                    break
                lines.append(line)
                if line == "data: [DONE]":
                    saw_done = True
                    break
            elapsed = int((time.monotonic() - started) * 1000)
            latency = first_chunk_ms if first_chunk_ms is not None else elapsed
            return response.status, content_type, latency, lines, saw_done
