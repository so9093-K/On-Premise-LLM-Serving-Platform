from __future__ import annotations

import httpx

from ai_model_serving.errors import ServiceError
from ai_model_serving.settings import RuntimeEndpoint
from ai_model_serving.upstream import (
    QUEUE_TIMEOUT_RETRY_AFTER_SECONDS,
    CircuitBreaker,
    VLLMClient,
    _counts_as_upstream_failure,
    _http_status_to_service_error,
)


def endpoint() -> RuntimeEndpoint:
    return RuntimeEndpoint("local-main", "http://runtime/v1", "local-main", 1)


def test_upstream_client_request_errors_are_validation_errors_not_502() -> None:
    for status in (400, 404, 422):
        exc = _http_status_to_service_error(endpoint(), status)
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
    exc = ServiceError("UPSTREAM_ERROR", "upstream failed", True, 502)
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
        # Just tripped, so remaining cooldown should be at (not past) reset_seconds.
        assert 14.0 < exc.retry_after_seconds <= 15.0
        headers = exc.to_response().headers
        assert headers["retry-after"] == "15"


def test_queue_timeout_error_carries_fixed_retry_after_hint() -> None:
    ep = RuntimeEndpoint("local-main", "http://runtime/v1", "local-main", 1, max_concurrency=1, queue_timeout_seconds=0.01)
    client = VLLMClient(ep)

    import anyio

    async def run() -> ServiceError:
        await client._semaphore.acquire()  # hold the only admission slot
        try:
            await client.post_json("chat/completions", {})
        except ServiceError as exc:
            return exc
        raise AssertionError("expected QUEUE_TIMEOUT")

    exc = anyio.run(run)
    assert exc.code == "QUEUE_TIMEOUT"
    assert exc.status_code == 503
    assert exc.retry_after_seconds == QUEUE_TIMEOUT_RETRY_AFTER_SECONDS
    headers = exc.to_response().headers
    assert headers["retry-after"] == "5"


def test_service_error_without_retry_after_omits_header() -> None:
    exc = ServiceError("VALIDATION_ERROR", "bad request", False, 422)
    assert "retry-after" not in exc.to_response().headers


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
