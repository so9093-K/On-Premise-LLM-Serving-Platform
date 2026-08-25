"""/metrics 노출, request_id/에러코드 접근 로그 기록, request body 크기 제한,
CORS 정책을 검증한다."""

from __future__ import annotations

import dataclasses
import io
import json
import logging

from .helpers import *  # noqa: F401,F403
from starlette.requests import Request
from ai_model_serving.logging_policy import (
    record_error_diagnosis,
    record_readiness_failure,
    record_request_response_preview,
    safe_request_log_record,
)
from ai_model_serving.metrics import Metrics
from ai_model_serving.errors import error_payload, error_response_headers
from ai_model_serving.settings import CorsSettings

def test_gateway_error_uses_incoming_request_id():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    response = client.get("/v1/models", headers={"x-request-id": "req_from_client"})
    assert response.status_code == 401
    assert response.json()["error"]["request_id"] == "req_from_client"
    assert response.headers["x-request-id"] == "req_from_client"
    assert response.headers["x-error-code"] == "UNAUTHORIZED"


def test_gateway_error_without_incoming_request_id_echoes_minted_id_in_header():
    # x-request-id를 안 보낸 클라이언트도, error_payload가 새로 발급한 request_id가
    # 응답 바디와 X-Request-Id 헤더에서 반드시 일치해야 접근 로그(logging_policy.py)가
    # 같은 값을 남길 수 있다 — 안 그러면 클라이언트가 보고한 request_id로 로그를
    # 절대 못 찾는다.
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    response = client.get("/v1/models")
    assert response.status_code == 401
    body_request_id = response.json()["error"]["request_id"]
    assert body_request_id
    assert response.headers["x-request-id"] == body_request_id
    assert response.headers["x-error-code"] == "UNAUTHORIZED"


def test_gateway_unhandled_exception_uses_common_error_schema():
    client = TestClient(create_gateway_app(settings(), ExplodingGatewayClients()), raise_server_exceptions=False)
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    Draft202012Validator(error_schema()).validate(response.json())


def test_gateway_service_error_response_includes_original_cause_debug():
    class CauseRuntimeClient(FakeRuntimeClient):
        async def post_json(self, path, payload, **kwargs):
            try:
                raise ValueError("audio decoder failed")
            except ValueError as cause:
                raise ServiceError("VALIDATION_ERROR", "Upstream rejected local-main.") from cause

    clients = FakeGatewayClients()
    clients.main_llm = CauseRuntimeClient(endpoint=clients.main_llm.endpoint)
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["debug"]["cause_type"] == "ValueError"
    assert body["error"]["debug"]["cause_message"] == "audio decoder failed"
    Draft202012Validator(error_schema()).validate(body)


def test_gateway_rejects_oversized_request_body():
    cfg = settings()
    cfg = dataclasses.replace(cfg, max_request_body_bytes=32)
    client = TestClient(create_gateway_app(cfg, FakeGatewayClients()))
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "x" * 100}]},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
    Draft202012Validator(error_schema()).validate(response.json())


def test_gateway_records_safe_validation_rejection_metric_for_image_errors():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}]},
    )
    metrics = client.get("/metrics").text
    assert 'request_validation_rejections_total{reason="image_input",service="gateway"}' in metrics


def test_gateway_records_specific_validation_rejection_reason_for_image_pixels():
    # 최소 24바이트 PNG 헤더(signature + IHDR + 3x3 크기, 실제 픽셀 데이터 없음):
    # _png_dimensions는 헤더만 읽고, settings()는 max_image_pixels를 4로
    # 제한하므로 3x3=9가 픽셀 한도를 구체적으로 건드린다(위에서 다룬 범용
    # "image_input" 버킷과는 구분된다).
    png_b64 = "iVBORw0KGgoAAAAASUhEUgAAAAMAAAAD"
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}}]}]},
    )
    metrics = client.get("/metrics").text
    assert 'request_validation_rejections_total{reason="image_pixels",service="gateway"}' in metrics


def test_access_log_records_client_host():
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/v1/models",
        "headers": [(b"x-request-id", b"req_123")],
        "client": ("10.0.0.10", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
        "query_string": b"",
        "path_params": {},
    })

    record = safe_request_log_record(
        service="gateway",
        request=request,
        status_code=200,
        elapsed_seconds=0.01234,
    )

    assert record["client_host"] == "10.0.0.10"


def _bare_request() -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/v1/models",
        "headers": [],
        "client": ("10.0.0.10", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
        "query_string": b"",
        "path_params": {},
    })


def test_access_log_records_error_code_and_response_echoed_request_id():
    # error_code/response_request_id는 응답 헤더(X-Error-Code/X-Request-Id)에서 온다 —
    # status_code 하나(예: 503)에 여러 code가 몰려도 로그에서 바로 구분하고,
    # x-request-id를 안 보낸 클라이언트의 에러도 로그의 request_id가 응답 바디와 일치하게 하려는 목적.
    record = safe_request_log_record(
        service="gateway",
        request=_bare_request(),
        status_code=503,
        elapsed_seconds=0.02,
        error_code="CIRCUIT_OPEN",
        error_message="upstream circuit is open; retry after cooldown.",
        response_request_id="req_minted_abc",
    )

    assert record["error_code"] == "CIRCUIT_OPEN"
    assert record["error_message"] == "upstream circuit is open; retry after cooldown."
    assert record["request_id"] == "req_minted_abc"


def test_access_log_records_allowlisted_error_diagnosis_only():
    request = _bare_request()
    record_error_diagnosis(
        request,
        code="UPSTREAM_ERROR",
        retryable=True,
        debug={"cause_type": "HTTPStatusError", "cause_message": "upstream rejected", "upstream_status": 502, "upstream_body": "do not log"},
    )

    record = safe_request_log_record(service="gateway", request=request, status_code=502, elapsed_seconds=0.02)

    assert record["error_code"] == "UPSTREAM_ERROR"
    assert record["error_retryable"] is True
    assert record["error_cause_type"] == "HTTPStatusError"
    assert record["error_cause_message"] == "upstream rejected"
    assert record["error_upstream_status"] == 502
    assert "upstream_body" not in record


def test_access_log_records_readiness_failure_summary():
    request = _bare_request()
    record_readiness_failure(
        request,
        {
            "status": "not_ready",
            "dependencies": [
                {"name": "main_llm_vllm", "status": "ready"},
                {"name": "risk_prompt_vllm", "status": "not_ready", "message": "READINESS_TIMEOUT"},
            ],
        },
    )

    record = safe_request_log_record(service="gateway", request=request, status_code=503, elapsed_seconds=0.02)

    assert record["readiness_status"] == "not_ready"
    assert record["readiness_dependencies"] == "risk_prompt_vllm"
    assert record["readiness_summary"] == "risk_prompt_vllm: READINESS_TIMEOUT"


def test_access_log_includes_masked_request_response_body_when_set_on_request_state():
    request = _bare_request()
    request.state.request_body_masked = "user: 이메일은 [EMAIL_ADDRESS]입니다"
    request.state.response_body_masked = "안녕하세요, 도와드릴까요?"

    record = safe_request_log_record(
        service="gateway",
        request=request,
        status_code=200,
        elapsed_seconds=0.01,
    )

    assert record["request_body"] == "user: 이메일은 [EMAIL_ADDRESS]입니다"
    assert record["response_body"] == "안녕하세요, 도와드릴까요?"


def test_request_response_preview_is_truncated_so_the_log_line_stays_one_docker_message():
    """긴 응답이 Docker의 16KiB 메시지 한도를 넘겨 로그 줄이 쪼개지면, 조각 각각이
    유효한 JSON이 아니라서 prompt_tokens 같은 뒤쪽 필드가 통째로 사라진다."""
    request = _bare_request()

    record_request_response_preview(request, request_text="가" * 20000, response_text="나" * 20000)

    record = safe_request_log_record(
        service="gateway",
        request=request,
        status_code=200,
        elapsed_seconds=0.01,
    )

    assert record["request_body"].endswith("…(truncated)")
    assert record["response_body"].endswith("…(truncated)")
    assert len(json.dumps(record, ensure_ascii=False).encode()) < 16 * 1024


def test_access_log_omits_request_response_body_when_not_set_on_request_state():
    # LOG_REQUEST_RESPONSE_BODY=false(기본값)일 때 gateway_inference.py는
    # request.state에 아무것도 안 남기므로, 이 필드들은 로그에 아예 없어야 한다.
    record = safe_request_log_record(
        service="gateway",
        request=_bare_request(),
        status_code=200,
        elapsed_seconds=0.01,
    )

    assert "request_body" not in record
    assert "response_body" not in record


def test_access_log_omits_error_fields_for_success_responses():
    record = safe_request_log_record(
        service="gateway",
        request=_bare_request(),
        status_code=200,
        elapsed_seconds=0.01,
    )

    assert "error_code" not in record
    assert "error_message" not in record


def test_access_log_middleware_emits_api_error_diagnosis_and_request_id():
    # gateway 로거는 service_logger()가 propagate=False로 자체 StreamHandler를 붙여서
    # 쓰기 때문에, pytest caplog(root logger 기반)가 아니라 이 로거에 직접 핸들러를
    # 붙여 실제 미들웨어(safe_request_logging_middleware)가 응답 헤더의
    # X-Error-Code/X-Request-Id를 로그 레코드로 옮기는지 end-to-end로 검증한다.
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("ai_model_serving.gateway")
    logger.addHandler(handler)
    try:
        client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
        response = client.get("/v1/models")
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 401
    body_request_id = response.json()["error"]["request_id"]

    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    completed = [r for r in lines if r.get("event") == "http_request_completed"]
    assert completed, f"no http_request_completed log record captured: {stream.getvalue()}"
    assert completed[-1]["error_code"] == "UNAUTHORIZED"
    assert completed[-1]["error_retryable"] is False
    assert completed[-1]["request_id"] == body_request_id
    assert completed[-1]["error_message"] == response.json()["error"]["message"]


def test_access_log_middleware_emits_readiness_dependency_summary():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("ai_model_serving.gateway")
    logger.addHandler(handler)
    try:
        clients = FakeGatewayClients()
        clients.main_llm.ready = False
        response = TestClient(create_gateway_app(settings(), clients)).get("/ready")
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 503
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
    completed = [r for r in lines if r.get("event") == "http_request_completed"]
    assert completed[-1]["readiness_status"] == "not_ready"
    assert completed[-1]["readiness_dependencies"] == "main_llm_vllm"
    assert "MODEL_UNAVAILABLE" in completed[-1]["readiness_summary"]


def test_error_response_headers_strip_crlf_from_client_supplied_message():
    # message에는 클라이언트가 보낸 값이 그대로 들어갈 수 있다(예: 잘못된
    # response_format.type이 에러 메시지에 그대로 echo됨 — chat_response_format.py 참고).
    # CRLF를 주입해 헤더를 밀어넣으려는 시도가 있어도 X-Error-Message 헤더에는 절대
    # 원본 CRLF가 남으면 안 된다(HTTP header injection).
    payload = error_payload(
        "VALIDATION_ERROR",
        "response_format.type is bogus\r\nX-Injected: evil; use one of: json_object.",
        False,
    )
    headers = error_response_headers("VALIDATION_ERROR", payload)
    assert "\r" not in headers["X-Error-Message"]
    assert "\n" not in headers["X-Error-Message"]
    assert "X-Injected" not in headers
    assert "bogus" in headers["X-Error-Message"]


def test_main_model_metrics_restore_persistent_state_without_operation_id_labels():
    metrics = Metrics("gateway")
    metrics.project_main_model(
        {
            "public_model": "local-main",
            "active_profile": {
                "id": "gemma4-12b-unified-fp8",
                "upstream_model_id": "RedHatAI/gemma-4-12B-it-FP8-Dynamic",
                "revision": "67e53491df7a281623fa740de61307d5c542b7f4",
                "compatibility": {"status": "unverified"},
            },
            "runtime_image": "vllm/vllm-openai@sha256:" + "a" * 64,
            "gate": "closed",
            "last_operation": {
                "id": "sensitive-operation-id",
                "status": "rolling_back",
                "error": "sensitive error text",
            },
            "stats": {
                "switch_requests": 3,
                "switch_successes": 1,
                "switch_failures": 2,
                "rollbacks": 1,
                "rollback_failures": 0,
                "last_switch_timestamp": 123,
                "last_switch_duration_seconds": 45,
            },
        }
    )
    text = metrics.response().body.decode()
    assert 'main_model_gate_open{service="gateway"} 0.0' in text
    assert 'main_model_operation_state{service="gateway",state="rolling_back"} 1.0' in text
    assert 'main_model_operation_state{service="gateway",state="preparing"} 0.0' in text
    assert 'main_model_switch_operations{result="failure",service="gateway"} 2.0' in text
    assert "sensitive-operation-id" not in text
    assert "sensitive error text" not in text


def test_cors_default_allows_any_origin():
    # 기본값(CorsSettings())은 전체 허용("*")이다 — local_open의 "네트워크 경계가
    # 접근 제어를 소유한다" 기조 및 vLLM 자체 기본값과 맞춘 것이라, 별도 설정 없이도
    # 어떤 origin이든 cross-origin 응답을 받을 수 있어야 한다.
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    response = client.get("/v1/models", headers={"Origin": "http://example-webui.test"})
    assert response.headers["access-control-allow-origin"] == "*"


def test_cors_disabled_when_explicitly_empty():
    # 더 엄격한 배포에서는 CORS_ALLOWED_ORIGINS를 빈 값으로 둬서 CORSMiddleware
    # 자체를 안 붙이는 옵트아웃이 가능해야 한다.
    cors_settings = dataclasses.replace(settings(), cors=CorsSettings(allowed_origins=()))
    client = TestClient(create_gateway_app(cors_settings, FakeGatewayClients()))
    response = client.get("/v1/models", headers={"Origin": "http://example-webui.test"})
    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_configured_origin_only():
    cors_settings = dataclasses.replace(settings(), cors=CorsSettings(allowed_origins=("http://example-webui.test",)))
    client = TestClient(create_gateway_app(cors_settings, FakeGatewayClients()))

    allowed = client.get("/v1/models", headers={"Origin": "http://example-webui.test"})
    assert allowed.headers["access-control-allow-origin"] == "http://example-webui.test"

    other = client.get("/v1/models", headers={"Origin": "http://not-allowed.test"})
    assert "access-control-allow-origin" not in other.headers


def test_cors_preflight_allows_authorization_header_for_configured_origin():
    cors_settings = dataclasses.replace(settings(), cors=CorsSettings(allowed_origins=("http://example-webui.test",)))
    client = TestClient(create_gateway_app(cors_settings, FakeGatewayClients()))

    preflight = client.request(
        "OPTIONS",
        "/v1/chat/completions",
        headers={
            "Origin": "http://example-webui.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://example-webui.test"
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()
