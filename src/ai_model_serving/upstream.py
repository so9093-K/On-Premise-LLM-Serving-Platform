from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from .errors import DEBUG_VALUE_LIMIT, ERROR_STATUS, ServiceError
from .settings import RuntimeEndpoint

# QUEUE_TIMEOUT에 대한 Retry-After 힌트: admission queue_timeout_seconds
# 자체(보통 1-2초)보다 의도적으로 크게 잡아서, 힌트대로 재시도하는 클라이언트가
# 여전히 admission slot을 점유 중인 요청과 곧바로 다시 충돌하지 않도록 한다.
# MAIN_MODEL_SWITCH_IN_PROGRESS / MAIN_MODEL_CONTROL_UNAVAILABLE에 이미
# 사용 중인 고정 힌트값과 일치시킨다.
QUEUE_TIMEOUT_RETRY_AFTER_SECONDS = 5.0


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int, reset_seconds: float) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.reset_seconds = max(0.1, reset_seconds)
        self.failure_count = 0
        self.open_until = 0.0

    def before_request(self, target: str) -> None:
        now = time.monotonic()
        if now < self.open_until:
            raise ServiceError(
                "CIRCUIT_OPEN",
                f"Upstream circuit is open: {target}",
                True,
                503,
                retry_after_seconds=self.open_until - now,
            )

    def record_success(self) -> None:
        self.failure_count = 0
        self.open_until = 0.0

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.open_until = time.monotonic() + self.reset_seconds




def _counts_as_upstream_failure(exc: ServiceError) -> bool:
    status = exc.status_code or 500
    return exc.retryable or status >= 500 or exc.code in {"MODEL_UNAVAILABLE", "UPSTREAM_ERROR", "UPSTREAM_TIMEOUT"}


def _platform_error_from_response(response: httpx.Response) -> ServiceError | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    message = error.get("message")
    retryable = error.get("retryable")
    request_id = error.get("request_id")
    if not isinstance(code, str) or ERROR_STATUS.get(code) != response.status_code:
        return None
    if not isinstance(message, str) or not isinstance(retryable, bool):
        return None
    return ServiceError(
        code,
        message,
        retryable,
        response.status_code,
        request_id if isinstance(request_id, str) else None,
        debug={
            "upstream_status": response.status_code,
            **({"upstream_request_id": request_id} if isinstance(request_id, str) else {}),
        },
    )


def _upstream_response_debug(response: httpx.Response) -> dict[str, Any]:
    raw_body = response.content[:DEBUG_VALUE_LIMIT]
    body = raw_body.decode(response.encoding or "utf-8", errors="replace")
    if len(response.content) > DEBUG_VALUE_LIMIT:
        body = f"{body}... [truncated]"
    return {
        "upstream_status": response.status_code,
        "upstream_reason": response.reason_phrase,
        "upstream_body": body,
    }


def _http_status_to_service_error(endpoint: RuntimeEndpoint, response_or_status: httpx.Response | int) -> ServiceError:
    if isinstance(response_or_status, httpx.Response):
        platform_error = _platform_error_from_response(response_or_status)
        if platform_error is not None:
            return platform_error
        status = response_or_status.status_code
        debug = _upstream_response_debug(response_or_status)
    else:
        status = response_or_status
        debug = {"upstream_status": status}
    if status == 429:
        return ServiceError("RATE_LIMITED", f"Upstream rate limited: {endpoint.logical_id}", True, 429, debug=debug)
    if status in {400, 404, 422}:
        return ServiceError(
            "VALIDATION_ERROR",
            f"Upstream rejected the request for {endpoint.logical_id} with HTTP {status}; check error.debug.upstream_body for the runtime reason and adjust the request.",
            False,
            422,
            debug=debug,
        )
    if status in {401, 403}:
        return ServiceError("UPSTREAM_ERROR", f"Upstream authorization failed: {endpoint.logical_id}", True, 502, debug=debug)
    return ServiceError("UPSTREAM_ERROR", f"Upstream failed: {endpoint.logical_id}", True, 502, debug=debug)

class VLLMClient:
    def __init__(self, endpoint: RuntimeEndpoint) -> None:
        self.endpoint = endpoint
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(endpoint.max_concurrency)
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=endpoint.circuit_breaker_failure_threshold,
            reset_seconds=endpoint.circuit_breaker_reset_seconds,
        )

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=self.endpoint.timeout_seconds,
                    write=10.0,
                    pool=5.0,
                ),
                limits=httpx.Limits(
                    max_keepalive_connections=self.endpoint.http_max_keepalive_connections,
                    max_connections=self.endpoint.http_max_connections,
                ),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _url(self, path: str) -> str:
        if path.startswith("/"):
            parts = urlsplit(self.endpoint.base_url)
            root = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
            return f"{root}{path}"
        return f"{self.endpoint.base_url.rstrip('/')}/{path.lstrip('/')}"

    async def _with_operational_guards(self, operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        self._circuit_breaker.before_request(self.endpoint.logical_id)
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self.endpoint.queue_timeout_seconds)
        except TimeoutError as exc:
            raise ServiceError(
                "QUEUE_TIMEOUT",
                f"Timed out waiting for upstream capacity: {self.endpoint.logical_id}",
                True,
                503,
                retry_after_seconds=QUEUE_TIMEOUT_RETRY_AFTER_SECONDS,
            ) from exc
        try:
            try:
                result = await operation()
            except ServiceError as exc:
                if _counts_as_upstream_failure(exc):
                    self._circuit_breaker.record_failure()
                raise
            else:
                self._circuit_breaker.record_success()
                return result
        finally:
            self._semaphore.release()

    async def post_json(self, path: str, payload: dict[str, Any], *, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            url = self._url(path)
            try:
                response = await self.client.post(url, json=payload, headers=dict(headers or {}))
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as exc:
                raise ServiceError("UPSTREAM_TIMEOUT", f"Upstream timed out: {self.endpoint.logical_id}", True, 504) from exc
            except httpx.HTTPStatusError as exc:
                raise _http_status_to_service_error(self.endpoint, exc.response) from exc
            except httpx.HTTPError as exc:
                raise ServiceError("MODEL_UNAVAILABLE", f"Upstream unavailable: {self.endpoint.logical_id}", True, 503) from exc
            except ValueError as exc:
                raise ServiceError("UPSTREAM_ERROR", f"Upstream returned invalid JSON: {self.endpoint.logical_id}", True, 502) from exc

        return await self._with_operational_guards(operation)


    async def stream_bytes(self, path: str, payload: dict[str, Any], *, headers: Mapping[str, str] | None = None) -> AsyncIterator[bytes]:
        """Stream upstream bytes with the same admission and circuit guards used by JSON calls.

        Chat streaming is a transport-level fast path: the Gateway validates the
        request before calling this method, checks the upstream HTTP status here,
        and then relays SSE bytes without buffering the full response body.
        """
        self._circuit_breaker.before_request(self.endpoint.logical_id)
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self.endpoint.queue_timeout_seconds)
        except TimeoutError as exc:
            raise ServiceError(
                "QUEUE_TIMEOUT",
                f"Timed out waiting for upstream capacity: {self.endpoint.logical_id}",
                True,
                503,
                retry_after_seconds=QUEUE_TIMEOUT_RETRY_AFTER_SECONDS,
            ) from exc

        url = self._url(path)
        try:
            try:
                async with self.client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers={"Accept": "text/event-stream", **dict(headers or {})},
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk
            except httpx.TimeoutException as exc:
                raise ServiceError("UPSTREAM_TIMEOUT", f"Upstream timed out: {self.endpoint.logical_id}", True, 504) from exc
            except httpx.HTTPStatusError as exc:
                raise _http_status_to_service_error(self.endpoint, exc.response) from exc
            except httpx.HTTPError as exc:
                raise ServiceError("MODEL_UNAVAILABLE", f"Upstream unavailable: {self.endpoint.logical_id}", True, 503) from exc
            else:
                self._circuit_breaker.record_success()
        except ServiceError as exc:
            if _counts_as_upstream_failure(exc):
                self._circuit_breaker.record_failure()
            raise
        finally:
            self._semaphore.release()

    async def get_json(self, path: str, *, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            url = self._url(path)
            try:
                response = await self.client.get(url, headers=dict(headers or {}))
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as exc:
                raise ServiceError("UPSTREAM_TIMEOUT", f"Upstream timed out: {self.endpoint.logical_id}", True, 504) from exc
            except httpx.HTTPStatusError as exc:
                raise _http_status_to_service_error(self.endpoint, exc.response) from exc
            except httpx.HTTPError as exc:
                raise ServiceError("MODEL_UNAVAILABLE", f"Upstream unavailable: {self.endpoint.logical_id}", True, 503) from exc
            except ValueError as exc:
                raise ServiceError("UPSTREAM_ERROR", f"Upstream returned invalid JSON: {self.endpoint.logical_id}", True, 502) from exc

        return await self._with_operational_guards(operation)

    async def probe_json(self, path: str, *, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        """GET JSON for readiness without mutating serving traffic guards.

        Startup probes may fail repeatedly while vLLM downloads or loads a model.
        Those failures should not open the circuit breaker that protects real
        inference traffic, and an already-open inference circuit should not hide
        the current readiness reason.
        """
        url = self._url(path)
        try:
            response = await self.client.get(url, headers=dict(headers or {}))
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise ServiceError("UPSTREAM_TIMEOUT", f"Upstream timed out: {self.endpoint.logical_id}", True, 504) from exc
        except httpx.HTTPStatusError as exc:
            raise _http_status_to_service_error(self.endpoint, exc.response) from exc
        except httpx.HTTPError as exc:
            raise ServiceError("MODEL_UNAVAILABLE", f"Upstream unavailable: {self.endpoint.logical_id}", True, 503) from exc
        except ValueError as exc:
            raise ServiceError("UPSTREAM_ERROR", f"Upstream returned invalid JSON: {self.endpoint.logical_id}", True, 502) from exc
