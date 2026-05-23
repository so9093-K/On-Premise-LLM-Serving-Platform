from __future__ import annotations

import httpx

from ai_model_serving.errors import ServiceError
from ai_model_serving.settings import RuntimeEndpoint
from ai_model_serving.upstream import VLLMClient, _counts_as_upstream_failure, _http_status_to_service_error


def endpoint() -> RuntimeEndpoint:
    return RuntimeEndpoint("local-main", "http://runtime/v1", "local-main", 1)


def test_upstream_client_request_errors_are_validation_errors_not_502() -> None:
    for status in (400, 404, 422):
        exc = _http_status_to_service_error(endpoint(), status)
        assert exc.code == "VALIDATION_ERROR"
        assert exc.status_code == 422
        assert exc.retryable is False
        assert not _counts_as_upstream_failure(exc)


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
    assert not _counts_as_upstream_failure(exc)


def test_upstream_retryable_errors_still_count_for_circuit_breaker() -> None:
    exc = ServiceError("UPSTREAM_ERROR", "upstream failed", True, 502)
    assert _counts_as_upstream_failure(exc)


def test_upstream_client_supports_root_relative_paths_for_vllm_non_v1_endpoints() -> None:
    client = VLLMClient(endpoint())
    assert client._url("score") == "http://runtime/v1/score"
    assert client._url("/pooling") == "http://runtime/pooling"


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
