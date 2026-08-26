"""VLLMClient/CircuitBreaker(upstream vLLM 호출 계층)를 검증한다: HTTP 상태를
플랫폼 에러 코드로 매핑, circuit breaker의 open/retry-after, admission
큐가 가득 찼을 때의 QUEUE_TIMEOUT, readiness probe가 circuit breaker를
우회하는지."""

from __future__ import annotations

import httpx

from ai_model_serving.errors import ServiceError, error_response
from ai_model_serving.settings import RuntimeEndpoint
from ai_model_serving.upstream import (
    QUEUE_TIMEOUT_RETRY_AFTER_SECONDS,
    CircuitBreaker,
    VLLMClient,
    _counts_as_upstream_failure,
    _http_status_to_service_error,
)


def _response_headers(exc: ServiceError):
    """프로덕션 예외 핸들러와 같은 경로로 응답 헤더를 만든다.

    app_kernel.service_error_handler가 ServiceError를 이 인자들로 error_response()에
    넘긴다. 예전엔 테스트가 ServiceError.to_response()를 불렀는데, 그 메서드는
    error_response()와 같은 일을 하는 두 번째 구현이면서 프로덕션 호출자가 하나도
    없었다 -- 즉 테스트만 살려두던 경로라 헤더 회귀를 실제로 막아주지 못했다.
    """
    return error_response(
        exc.code,
        exc.message,
        exc.retryable,
        exc.status_code,
        None,
        exc.param,
        None,
        exc.retry_after_seconds,
    ).headers


def endpoint() -> RuntimeEndpoint:
    return RuntimeEndpoint("local-main", "http://runtime/v1", "local-main", 1)


def test_upstream_client_request_errors_are_validation_errors_not_502() -> None:
    # 404/422는 별도로 두지 않는다: _http_status_to_service_error()가
    # `if status in {400, 404, 422}:`라는 동일한 set-membership 분기로 처리하므로
    # 세 값 모두 완전히 같은 코드 경로를 탄다.
    exc = _http_status_to_service_error(endpoint(), 400)
    assert exc.code == "VALIDATION_ERROR"
    assert exc.status_code == 422
    assert exc.retryable is False
    assert not _counts_as_upstream_failure(exc)


def test_upstream_http_error_preserves_status_and_body_debug() -> None:
    response = httpx.Response(400, text="audio decoder failed")

    exc = _http_status_to_service_error(endpoint(), response)

    assert exc.code == "VALIDATION_ERROR"
    assert exc.debug == {
        "upstream_status": 400,
        "upstream_reason": "Bad Request",
        "upstream_body": "audio decoder failed",
    }


def test_upstream_platform_error_payload_is_preserved_for_gateway_risk_forwarding() -> None:
    response = httpx.Response(
        410,
        json={
            "error": {
                "code": "DETECTOR_DISABLED",
                "message": "Risk detector is not enabled: prompt",
                "retryable": False,
                "request_id": "req_disabled",
            }
        },
    )

    exc = _http_status_to_service_error(endpoint(), response)

    assert exc.code == "DETECTOR_DISABLED"
    assert exc.status_code == 410
    assert exc.retryable is False
    assert exc.request_id == "req_disabled"
    assert exc.debug == {"upstream_status": 410, "upstream_request_id": "req_disabled"}
    assert not _counts_as_upstream_failure(exc)


def test_upstream_retryable_errors_still_count_for_circuit_breaker() -> None:
    exc = ServiceError("UPSTREAM_ERROR", "upstream failed")
    assert _counts_as_upstream_failure(exc)


def test_upstream_client_supports_root_relative_paths_for_vllm_non_v1_endpoints() -> None:
    client = VLLMClient(endpoint())
    assert client._url("score") == "http://runtime/v1/score"
    assert client._url("/pooling") == "http://runtime/pooling"


def test_circuit_open_error_carries_retry_after_close_to_remaining_cooldown() -> None:
    breaker = CircuitBreaker(failure_threshold=1, reset_seconds=15.0)
    breaker.record_failure()

    try:
        breaker.before_request("local-main")
        raise AssertionError("expected CIRCUIT_OPEN")
    except ServiceError as exc:
        assert exc.code == "CIRCUIT_OPEN"
        assert exc.retry_after_seconds is not None
        # 방금 막 트립됐으니, 남은 cooldown은 reset_seconds를 넘지 않고 그 근처여야 한다.
        assert 14.0 < exc.retry_after_seconds <= 15.0
        assert _response_headers(exc)["retry-after"] == "15"


def test_queue_timeout_error_carries_fixed_retry_after_hint() -> None:
    ep = RuntimeEndpoint("local-main", "http://runtime/v1", "local-main", 1, max_concurrency=1, queue_timeout_seconds=0.01)
    client = VLLMClient(ep)

    import anyio

    async def run() -> ServiceError:
        await client._semaphore.acquire()  # 유일한 admission slot을 붙잡아둔다
        try:
            await client.post_json("chat/completions", {})
        except ServiceError as exc:
            return exc
        raise AssertionError("expected QUEUE_TIMEOUT")

    exc = anyio.run(run)
    assert exc.code == "QUEUE_TIMEOUT"
    assert exc.status_code == 503
    assert exc.retry_after_seconds == QUEUE_TIMEOUT_RETRY_AFTER_SECONDS
    assert _response_headers(exc)["retry-after"] == "5"


def test_service_error_without_retry_after_omits_header() -> None:
    exc = ServiceError("VALIDATION_ERROR", "bad request")
    assert "retry-after" not in _response_headers(exc)


def test_readiness_probe_bypasses_open_circuit_breaker() -> None:
    client = VLLMClient(endpoint())
    client._circuit_breaker.record_failure()
    client._circuit_breaker.record_failure()
    client._circuit_breaker.record_failure()

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"object": "list", "data": []}

    class FakeHttpClient:
        is_closed = False

        async def get(self, url, headers=None):
            return FakeResponse()

    client._client = FakeHttpClient()

    import anyio

    assert anyio.run(client.probe_json, "models") == {"object": "list", "data": []}
