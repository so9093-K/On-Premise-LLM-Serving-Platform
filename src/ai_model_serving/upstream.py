from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from .errors import DIAGNOSTIC_VALUE_LIMIT, ERROR_STATUS, ServiceError, record_queue_wait
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
                "CIRCUIT_OPEN", f"Upstream circuit is open: {target}", retry_after_seconds=self.open_until - now,
            )

    def record_success(self) -> None:
        self.failure_count = 0
        self.open_until = 0.0

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.open_until = time.monotonic() + self.reset_seconds




def _counts_as_upstream_failure(exc: ServiceError) -> bool:
    return exc.retryable or exc.status_code >= 500 or exc.code in {"MODEL_UNAVAILABLE", "UPSTREAM_ERROR", "UPSTREAM_TIMEOUT"}


class _AdmissionScope:
    """upstream 호출 한 건의 circuit-breaker 판정과 동시성 slot 점유를 함께 소유한다.

    JSON 호출과 SSE relay는 같은 admission 규칙을 써야 한다. 예전에는 두 경로가
    circuit breaker 확인, semaphore 획득, queue timeout 매핑을 각자 복제하고 있어서
    한쪽만 고치면 다른 쪽이 조용히 뒤처졌다. 두 경로가 이 scope 하나만 쓰도록 해
    그 drift가 생길 자리를 없앤다.

    ``acquire``는 slot을 못 잡으면 QUEUE_TIMEOUT을 올리고, circuit이 열려 있으면
    CIRCUIT_OPEN을 올린다. 둘 다 slot을 잡기 전이므로 ``release``는 부르지 않는다.
    """

    __slots__ = ("_endpoint", "_circuit_breaker", "_semaphore", "_held", "queue_wait_seconds")

    def __init__(self, endpoint: RuntimeEndpoint, circuit_breaker: CircuitBreaker, semaphore: asyncio.Semaphore) -> None:
        self._endpoint = endpoint
        self._circuit_breaker = circuit_breaker
        self._semaphore = semaphore
        self._held = False
        self.queue_wait_seconds = 0.0

    async def acquire(self) -> None:
        self._circuit_breaker.before_request(self._endpoint.logical_id)
        waiting_since = time.monotonic()
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self._endpoint.queue_timeout_seconds)
        except TimeoutError as exc:
            raise ServiceError(
                "QUEUE_TIMEOUT", f"Timed out waiting for upstream capacity: {self._endpoint.logical_id}", retry_after_seconds=QUEUE_TIMEOUT_RETRY_AFTER_SECONDS,
            ) from exc
        self._held = True
        self.queue_wait_seconds = time.monotonic() - waiting_since
        # 느린 요청의 원인을 대기와 추론으로 가를 수 있도록 요청 단위 로그에 남긴다.
        # Prometheus latency histogram은 둘을 합친 값만 보여준다.
        record_queue_wait(self.queue_wait_seconds)

    def release(self, exc: BaseException | None) -> None:
        """slot을 반납하고 circuit breaker에 결과를 반영한다.

        ``ServiceError``가 아닌 예외(client 취소로 인한 ``GeneratorExit``/
        ``CancelledError`` 등)는 upstream 건강도의 근거가 아니므로 breaker를
        움직이지 않는다.
        """
        if not self._held:
            return
        self._held = False
        try:
            if exc is None:
                self._circuit_breaker.record_success()
            elif isinstance(exc, ServiceError) and _counts_as_upstream_failure(exc):
                self._circuit_breaker.record_failure()
        finally:
            self._semaphore.release()

    async def __aenter__(self) -> "_AdmissionScope":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any) -> bool:
        self.release(exc)
        return False


class UpstreamStream:
    """admission을 이미 통과한 upstream SSE 본문이다.

    admission 거부는 응답 헤더가 정해지기 전에 올라와야 한다. 예전에는 admission이
    async generator 안에 있어서 첫 ``__anext__``, 즉 200 헤더가 나간 뒤에야 판정됐고
    QUEUE_TIMEOUT/CIRCUIT_OPEN이 문서로 선언한 503 + ``Retry-After`` 대신 200 + SSE
    오류 event로 나갔다. ``open_stream``이 slot을 먼저 잡고 이 handle을 돌려주므로
    거부는 라우터의 일반 오류 경로를 그대로 탄다.

    slot 반납은 본문 generator의 ``finally``가 소유한다. 호출자는 끝까지 읽거나
    ``aclose()``로 닫아야 하며, 닫지 않으면 GC 시점까지 slot이 남는다.
    """

    __slots__ = ("_chunks", "queue_wait_seconds")

    def __init__(self, chunks: AsyncIterator[bytes], *, queue_wait_seconds: float) -> None:
        self._chunks = chunks
        self.queue_wait_seconds = queue_wait_seconds

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._chunks.__aiter__()

    async def aclose(self) -> None:
        await self._chunks.aclose()


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
    # upstream이 보낸 retryable은 envelope 형태 검증용으로만 읽고 전달하지 않는다.
    # retryable은 code로 결정되므로(errors.ERROR_RETRYABLE), upstream이 같은 code에
    # 다른 값을 실어 보내도 우리 계약이 흔들리지 않는다.
    return ServiceError(
        code, message, request_id=request_id if isinstance(request_id, str) else None, diagnostics={
            "upstream_status": response.status_code,
            **({"upstream_request_id": request_id} if isinstance(request_id, str) else {}),
        },
    )


def _upstream_response_diagnostics(response: httpx.Response) -> dict[str, Any]:
    raw_body = response.content[:DIAGNOSTIC_VALUE_LIMIT]
    body = raw_body.decode(response.encoding or "utf-8", errors="replace")
    if len(response.content) > DIAGNOSTIC_VALUE_LIMIT:
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
        diagnostics = _upstream_response_diagnostics(response_or_status)
    else:
        status = response_or_status
        diagnostics = {"upstream_status": status}
    if status == 429:
        return ServiceError("RATE_LIMITED", f"Upstream rate limited: {endpoint.logical_id}", diagnostics=diagnostics)
    if status in {400, 404, 422}:
        return ServiceError(
            "VALIDATION_ERROR", f"Upstream rejected the request for {endpoint.logical_id} with HTTP {status}; adjust the request and use request_id for operational diagnosis.", diagnostics=diagnostics,
        )
    if status in {401, 403}:
        return ServiceError("UPSTREAM_ERROR", f"Upstream authorization failed: {endpoint.logical_id}", diagnostics=diagnostics, diagnostic_code="UPSTREAM_AUTH_FAILED")
    return ServiceError("UPSTREAM_ERROR", f"Upstream failed: {endpoint.logical_id}", diagnostics=diagnostics, diagnostic_code="UPSTREAM_HTTP_ERROR")

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

    def _admission(self) -> _AdmissionScope:
        return _AdmissionScope(self.endpoint, self._circuit_breaker, self._semaphore)

    async def _with_operational_guards(self, operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        async with self._admission():
            return await operation()

    async def post_json(self, path: str, payload: dict[str, Any], *, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            url = self._url(path)
            try:
                response = await self.client.post(url, json=payload, headers=dict(headers or {}))
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as exc:
                raise ServiceError("UPSTREAM_TIMEOUT", f"Upstream timed out: {self.endpoint.logical_id}") from exc
            except httpx.HTTPStatusError as exc:
                raise _http_status_to_service_error(self.endpoint, exc.response) from exc
            except httpx.HTTPError as exc:
                raise ServiceError("MODEL_UNAVAILABLE", f"Upstream unavailable: {self.endpoint.logical_id}") from exc
            except ValueError as exc:
                raise ServiceError("UPSTREAM_RESPONSE_INVALID", f"Upstream returned invalid JSON: {self.endpoint.logical_id}") from exc

        return await self._with_operational_guards(operation)


    async def open_stream(self, path: str, payload: dict[str, Any], *, headers: Mapping[str, str] | None = None) -> UpstreamStream:
        """Admit the request, then hand back the upstream SSE body.

        Chat streaming is a transport-level fast path: the Gateway validates the
        request before calling this method, checks the upstream HTTP status while
        relaying, and never buffers the full response body.  Admission uses the
        same ``_AdmissionScope`` as the JSON calls and is resolved here, before
        the caller commits response headers.
        """
        scope = self._admission()
        await scope.acquire()
        return UpstreamStream(
            self._relay_chunks(scope, path, payload, headers),
            queue_wait_seconds=scope.queue_wait_seconds,
        )

    async def _relay_chunks(
        self,
        scope: _AdmissionScope,
        path: str,
        payload: dict[str, Any],
        headers: Mapping[str, str] | None,
    ) -> AsyncIterator[bytes]:
        url = self._url(path)
        failure: BaseException | None = None
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
                raise ServiceError("UPSTREAM_TIMEOUT", f"Upstream timed out: {self.endpoint.logical_id}") from exc
            except httpx.HTTPStatusError as exc:
                raise _http_status_to_service_error(self.endpoint, exc.response) from exc
            except httpx.HTTPError as exc:
                raise ServiceError("MODEL_UNAVAILABLE", f"Upstream unavailable: {self.endpoint.logical_id}") from exc
        except BaseException as exc:
            failure = exc
            raise
        finally:
            # client 취소로 generator가 닫히는 경로에서도 반드시 지나간다.
            # release는 동기 함수라 aclose 중에 suspend되지 않는다.
            scope.release(failure)

    async def get_json(self, path: str, *, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            url = self._url(path)
            try:
                response = await self.client.get(url, headers=dict(headers or {}))
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as exc:
                raise ServiceError("UPSTREAM_TIMEOUT", f"Upstream timed out: {self.endpoint.logical_id}") from exc
            except httpx.HTTPStatusError as exc:
                raise _http_status_to_service_error(self.endpoint, exc.response) from exc
            except httpx.HTTPError as exc:
                raise ServiceError("MODEL_UNAVAILABLE", f"Upstream unavailable: {self.endpoint.logical_id}") from exc
            except ValueError as exc:
                raise ServiceError("UPSTREAM_ERROR", f"Upstream returned invalid JSON: {self.endpoint.logical_id}") from exc

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
            raise ServiceError("UPSTREAM_TIMEOUT", f"Upstream timed out: {self.endpoint.logical_id}") from exc
        except httpx.HTTPStatusError as exc:
            raise _http_status_to_service_error(self.endpoint, exc.response) from exc
        except httpx.HTTPError as exc:
            raise ServiceError("MODEL_UNAVAILABLE", f"Upstream unavailable: {self.endpoint.logical_id}") from exc
        except ValueError as exc:
            raise ServiceError("UPSTREAM_ERROR", f"Upstream returned invalid JSON: {self.endpoint.logical_id}") from exc
