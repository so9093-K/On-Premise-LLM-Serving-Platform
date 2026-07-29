from __future__ import annotations

import base64
import json
import time
import urllib.request
from typing import Any

from .config import RuntimeValidationConfig


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

        The returned event lines are intentionally capped to protocol metadata
        checks.  The validator only inspects SSE framing, first chunk timing, and
        DONE visibility; it does not persist token deltas or generated content.
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
        saw_done = False
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            content_type = response.headers.get("content-type", "")
            for raw_line in response:
                if first_chunk_ms is None:
                    first_chunk_ms = int((time.monotonic() - started) * 1000)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line:
                    lines.append(line[:256])
                if line == "data: [DONE]":
                    saw_done = True
                    break
                if len(lines) >= 20:
                    break
            elapsed = int((time.monotonic() - started) * 1000)
            return response.status, content_type, first_chunk_ms or elapsed, lines, saw_done
