"""client 층 측정. 소켓에서 관찰 가능한 것만 잰다(ADR-0026 3절).

client는 chunk 경계만 볼 수 있고 그 안에 토큰이 몇 개인지 모른다. 그래서
time_to_first_chunk와 chunk 간격을 재고, 토큰 기준 값은 응답 usage의 개수로
나눈 근사값이라는 것을 이름과 계약이 밝힌다.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

_DONE = "[DONE]"


@dataclass
class RequestSample:
    """요청 하나의 원시 관찰값. percentile은 이 샘플들에서 계산한다."""

    index: int
    succeeded: bool
    status_code: int | None = None
    error_code: str | None = None
    request_id: str | None = None
    upstream_response_id: str | None = None
    client_time_to_first_chunk_seconds: float | None = None
    client_operation_duration_seconds: float | None = None
    client_time_per_output_chunk_seconds: list[float] = field(default_factory=list)
    client_input_tokens: int | None = None
    client_output_tokens: int | None = None

    @property
    def client_time_per_output_token_seconds(self) -> float | None:
        """계약이 선언한 토큰 기준 근사값(metrics.yaml).

        첫 토큰은 TTFC에 이미 들어 있으므로 나머지 토큰 수로 나눈다. chunk 간격의
        percentile은 전송 계층이 chunk를 뭉쳐 보내면 서버가 아니라 그 뭉침을 재지만,
        이 값은 생성 구간 전체를 토큰 수로 나누므로 뭉침의 영향을 받지 않는다.
        """
        if self.client_output_tokens is None or self.client_output_tokens < 2:
            return None
        if self.client_operation_duration_seconds is None or self.client_time_to_first_chunk_seconds is None:
            return None
        span = self.client_operation_duration_seconds - self.client_time_to_first_chunk_seconds
        return span / (self.client_output_tokens - 1) if span >= 0 else None

    def as_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {"index": self.index, "succeeded": self.succeeded}
        for key in (
            "status_code", "error_code", "request_id", "upstream_response_id",
            "client_time_to_first_chunk_seconds", "client_operation_duration_seconds",
            "client_time_per_output_token_seconds",
            "client_input_tokens", "client_output_tokens",
        ):
            value = getattr(self, key)
            if value is not None:
                document[key] = value
        if self.client_time_per_output_chunk_seconds:
            document["client_time_per_output_chunk_seconds"] = self.client_time_per_output_chunk_seconds
        return document


def _parse_sse_payload(line: str) -> dict[str, Any] | None:
    if not line.startswith("data:"):
        return None
    data = line[len("data:"):].strip()
    if not data or data == _DONE:
        return None
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _carries_output(event: dict[str, Any]) -> bool:
    """이 chunk가 실제 출력 토큰을 실어 왔는가.

    SSE 스트림의 마지막에는 usage만 담은 chunk와 ``[DONE]``이 따라온다. 그것들의
    도착 간격을 출력 chunk 간격으로 세면 값이 실제보다 작게 나온다. 처음 측정에서
    5개 chunk에 대해 간격이 6개 나왔고 마지막 값이 21마이크로초였다.
    """
    for choice in event.get("choices") or []:
        delta = choice.get("delta") if isinstance(choice, dict) else None
        if not isinstance(delta, dict):
            continue
        if delta.get("content") or delta.get("tool_calls") or delta.get("reasoning_content"):
            return True
    return False


def stream_once(
    client: httpx.Client,
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    index: int,
) -> RequestSample:
    """streaming 요청 하나를 보내고 출력 chunk 도착 시각을 기록한다."""
    sample = RequestSample(index=index, succeeded=False)
    started = time.perf_counter()
    previous: float | None = None
    try:
        with client.stream("POST", url, json=payload, headers=headers) as response:
            sample.status_code = response.status_code
            sample.request_id = response.headers.get("x-request-id")
            if response.status_code != 200:
                body = response.read().decode("utf-8", errors="replace")
                sample.error_code = _error_code(body)
                sample.client_operation_duration_seconds = time.perf_counter() - started
                return sample
            for line in response.iter_lines():
                event = _parse_sse_payload(line.strip())
                if event is None:
                    continue
                # 오류는 헤더가 나간 뒤 SSE event로 올 수 있다. 그 요청은 성공이 아니다.
                if isinstance(event.get("error"), dict):
                    sample.error_code = str(event["error"].get("code") or "STREAM_ERROR")
                if sample.upstream_response_id is None and isinstance(event.get("id"), str):
                    sample.upstream_response_id = event["id"]
                usage = event.get("usage")
                if isinstance(usage, dict):
                    sample.client_input_tokens = usage.get("prompt_tokens")
                    sample.client_output_tokens = usage.get("completion_tokens")
                if not _carries_output(event):
                    continue
                now = time.perf_counter()
                if previous is None:
                    sample.client_time_to_first_chunk_seconds = now - started
                else:
                    sample.client_time_per_output_chunk_seconds.append(now - previous)
                previous = now
    except httpx.HTTPError as exc:
        sample.error_code = type(exc).__name__
        sample.client_operation_duration_seconds = time.perf_counter() - started
        return sample

    sample.client_operation_duration_seconds = time.perf_counter() - started
    sample.succeeded = sample.error_code is None and sample.client_time_to_first_chunk_seconds is not None
    return sample


def _error_code(body: str) -> str:
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        return "NON_JSON_ERROR"
    error = document.get("error") if isinstance(document, dict) else None
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    return "UNKNOWN_ERROR"
