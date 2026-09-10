"""chat completion 요청/응답 검증(사전 거부), SSE 스트리밍(사용량/청크 메트릭,
중간에 실패해도 SSE 에러 이벤트로 끝맺음, chunk 상한), tool_calls/logprobs/
logit_bias 조합을 검증한다."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from .helpers import *  # noqa: F401,F403

def test_gateway_rejects_invalid_payloads_before_upstream_call():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))
    chat = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": []},
    )
    assert chat.status_code == 422
    assert clients.main_llm.last_path is None
    Draft202012Validator(error_schema()).validate(chat.json())

    embed = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": [], "dimensions": 42},
    )
    assert embed.status_code == 422
    assert clients.embedding_clients["local-embed"].last_path is None
    Draft202012Validator(error_schema()).validate(embed.json())

    embed_zero_truncate = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "truncate_prompt_tokens": 0},
    )
    assert embed_zero_truncate.status_code == 422
    assert clients.embedding_clients["local-embed"].last_path is None

    embed_unknown_format = client.post(
        "/v1/embeddings",
        headers=auth_headers(),
        json={"model": "local-embed", "input": ["hello"], "encoding_format": "int8"},
    )
    assert embed_unknown_format.status_code == 422
    assert clients.embedding_clients["local-embed"].last_path is None


def test_gateway_rejects_invalid_upstream_response_schema():
    clients = FakeGatewayClients()
    clients.main_llm = FakeRuntimeClient({"object": "chat.completion", "model": "wrong", "choices": []})
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_RESPONSE_INVALID"


def test_gateway_metrics_records_http_and_upstream_counts():
    client = TestClient(create_gateway_app(settings(), FakeGatewayClients()))
    client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}]},
    )
    response = client.get("/metrics")
    assert response.headers["content-type"].startswith("text/plain")
    metrics = response.text
    assert not metrics.rstrip().endswith("# EOF")
    assert 'http_requests_total{route="/v1/chat/completions",service="gateway",status_code="200"}' in metrics
    assert 'upstream_request_duration_seconds_count{path="chat/completions",service="gateway",target="local-main"}' in metrics


def test_gateway_accepts_streaming_and_rejects_tool_calling_contracts():
    clients = FakeGatewayClients()
    client = TestClient(create_gateway_app(settings(), clients))

    streaming = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "stream_options": {"include_usage": True}, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert streaming.status_code == 200
    assert streaming.headers["content-type"].startswith("text/event-stream")
    assert streaming.headers["cache-control"] == "no-cache"
    assert streaming.headers["x-accel-buffering"] == "no"
    body = streaming.content.decode()
    assert "data: [DONE]" in body
    assert clients.main_llm.last_path == "chat/completions"
    assert clients.main_llm.last_payload["stream"] is True
    assert clients.main_llm.last_payload["stream_options"] == {"include_usage": True}

    clients.main_llm.last_path = None
    streaming_string = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": "true", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert streaming_string.status_code == 422
    assert clients.main_llm.last_path is None

    invalid_stream_options = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "stream_options": {"include_usage": "yes"}, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert invalid_stream_options.status_code == 422
    assert clients.main_llm.last_path is None

    stream_options_without_stream = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream_options": {"include_usage": True}, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert stream_options_without_stream.status_code == 422
    assert clients.main_llm.last_path is None

    streaming_false = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": False, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert streaming_false.status_code == 200
    clients.main_llm.last_path = None

    tools = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "tools": [], "messages": [{"role": "user", "content": "hello"}]},
    )
    assert tools.status_code == 422
    assert clients.main_llm.last_path is None

    tool_role = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "tool", "content": "hello"}]},
    )
    assert tool_role.status_code == 422
    assert clients.main_llm.last_path is None

    tool_calls = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "assistant", "content": "hello", "tool_calls": []}]},
    )
    assert tool_calls.status_code == 422
    assert clients.main_llm.last_path is None


def test_gateway_streaming_reports_usage_and_chunk_metrics():
    clients = FakeGatewayClients()
    clients.main_llm.stream_chunks = [
        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n',
        b'data: [DONE]\n\n',
    ]
    client = TestClient(create_gateway_app(settings(), clients))
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200
    assert '"usage"' in response.content.decode()

    metrics = client.get("/metrics").text
    assert 'streaming_chunks_total{service="gateway",target="local-main"}' in metrics
    assert 'streaming_bytes_total{service="gateway",target="local-main"}' in metrics
    assert 'streaming_usage_events_total{service="gateway",target="local-main"} 1.0' in metrics


def test_gateway_streaming_emits_sse_error_before_first_chunk():
    clients = FakeGatewayClients()
    clients.main_llm = StreamingErrorRuntimeClient(fail_after_first_chunk=False)
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "event: error" in body
    assert "UPSTREAM_TIMEOUT" in body
    assert "data: [DONE]" in body

    metrics = client.get("/metrics").text
    assert 'streaming_errors_total{code="UPSTREAM_TIMEOUT",phase="before_first_chunk",service="gateway",target="local-main"} 1.0' in metrics


def test_gateway_streaming_emits_sse_error_after_partial_chunk():
    clients = FakeGatewayClients()
    clients.main_llm = StreamingErrorRuntimeClient(fail_after_first_chunk=True)
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "partial" in body
    assert "event: error" in body
    assert "data: [DONE]" in body

    metrics = client.get("/metrics").text
    assert 'streaming_errors_total{code="UPSTREAM_TIMEOUT",phase="mid_stream",service="gateway",target="local-main"} 1.0' in metrics


def test_gateway_streaming_limit_exceeded_is_not_retryable(monkeypatch, tmp_path):
    monkeypatch.setenv("REQUEST_EVENT_LOG_DIR", str(tmp_path))
    clients = FakeGatewayClients()
    clients.main_llm.stream_chunks = [
        b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"second"}}]}\n\n',
    ]
    client = TestClient(create_gateway_app(replace(settings(), streaming_max_chunks=1), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "event: error" in body
    error_event = body.split("event: error", 1)[1]
    error_line = next(line for line in error_event.splitlines() if line.startswith("data: {"))
    payload = json.loads(error_line.removeprefix("data: "))
    assert payload["error"]["code"] == "STREAM_LIMIT_EXCEEDED"
    assert payload["error"]["retryable"] is False
    assert payload["error"]["request_id"] == response.headers["x-request-id"]
    records = [json.loads(line) for line in (tmp_path / "gateway.jsonl").read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["status_code"] == 200
    assert records[0]["error_code"] == "STREAM_LIMIT_EXCEEDED"
    assert records[0]["diagnostic_code"] == "STREAM_CHUNK_LIMIT_EXCEEDED"
    assert records[0]["request_id"] == payload["error"]["request_id"]


def test_gateway_accepts_upstream_tool_call_response_schema():
    clients = FakeGatewayClients()
    clients.main_llm = FakeRuntimeClient({
        "id": "chatcmpl_tool",
        "object": "chat.completion",
        "created": 1,
        "model": "local-main",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_weather",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": "{\"city\":\"Seoul\"}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    })
    client = TestClient(create_gateway_app(tool_calling_settings(), clients))
    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={
            "model": "local-main",
            "messages": [{"role": "user", "content": "서울 날씨"}],
            "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
            "tool_choice": "auto",
        },
    )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_weather"


def test_gateway_logprobs_logit_bias_and_stream_contracts():
    clients = FakeGatewayClients()
    clients.main_llm.post_response["choices"][0]["logprobs"] = {
        "content": [{"token": "ok", "logprob": -0.1, "bytes": [111, 107], "top_logprobs": [{"token": "ok", "logprob": -0.1, "bytes": [111, 107]}]}],
        "refusal": None,
    }
    client = TestClient(create_gateway_app(advanced_chat_settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], "logprobs": True, "top_logprobs": 2},
    )
    assert response.status_code == 200
    assert clients.main_llm.last_payload["logprobs"] is True
    assert clients.main_llm.last_payload["top_logprobs"] == 2

    # {"top_logprobs": 1}(logprobs 미지정)은 별도로 두지 않는다: _validate_logprobs()의
    # `payload.get("logprobs") is not True` 분기는 None과 False를 구분하지 않으므로
    # {"logprobs": False, ...}와 동일한 코드 경로를 탄다.
    for payload in [
        {"logprobs": False, "top_logprobs": 0},
        {"logprobs": True, "top_logprobs": 11},
    ]:
        invalid = client.post("/v1/chat/completions", headers=auth_headers(), json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], **payload})
        assert invalid.status_code == 422

    valid_bias = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], "logit_bias": {"42": -1.5}},
    )
    assert valid_bias.status_code == 200
    assert clients.main_llm.last_payload["logit_bias"] == {"42": -1.5}

    # {"1": True}는 별도로 두지 않는다: _validate_logit_bias()에서 `not is_number(bias)`와
    # `bias < min_bias or bias > max_bias`가 같은 한 줄의 or절이라 {"1": 101}과 결과가 같다.
    for bias in [{"x": 1}, {"1": 101}, {str(i): 0 for i in range(257)}]:
        invalid = client.post("/v1/chat/completions", headers=auth_headers(), json={"model": "local-main", "messages": [{"role": "user", "content": "hello"}], "logit_bias": bias})
        assert invalid.status_code == 422

    clients.main_llm.stream_chunks = [b'data: {"choices":[{"delta":{"content":"ok"},"logprobs":{"content":[]}}]}\n\n', b"data: [DONE]\n\n"]
    stream = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "messages": [{"role": "user", "content": "hello"}], "logprobs": True},
    )
    assert stream.status_code == 200
    assert '"logprobs"' in stream.content.decode()


def test_streaming_request_event_carries_token_counts_and_terminal_status(monkeypatch, tmp_path):
    """streaming도 non-stream과 같은 필드로 request_id 단위 조회가 되어야 한다.

    예전에는 relay가 usage 객체를 세기만 하고 숫자를 버려서, 업스트림이 112
    토큰을 보고했는데도 streaming 요청 이벤트에는 토큰 필드가 하나도 없었다.
    """
    monkeypatch.setenv("REQUEST_EVENT_LOG_DIR", str(tmp_path))
    usage = {"prompt_tokens": 11, "completion_tokens": 101, "total_tokens": 112}
    clients = FakeGatewayClients()
    clients.main_llm.stream_chunks = [
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
        f'data: {{"choices":[],"usage":{json.dumps(usage)}}}\n\n'.encode(),
        b"data: [DONE]\n\n",
    ]
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    records = [json.loads(line) for line in (tmp_path / "gateway.jsonl").read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["prompt_tokens"] == 11
    assert records[0]["completion_tokens"] == 101
    assert records[0]["total_tokens"] == 112
    assert records[0]["stream_status"] == "completed"


def test_streaming_admission_rejection_answers_503_before_response_headers():
    """admission 거부는 SSE 전송을 고르기 전에 판정되어야 한다.

    예전에는 admission이 relay generator 안에 있어서 첫 chunk를 당길 때, 즉 이미
    200 헤더가 나간 뒤에 판정됐고 QUEUE_TIMEOUT이 200 + SSE 오류 event로 나갔다.
    이 route가 OpenAPI로 선언한 계약은 503 + Retry-After다.
    """

    class QueueFullClient(FakeRuntimeClient):
        async def open_stream(self, path, payload, **kwargs):
            raise ServiceError(
                "QUEUE_TIMEOUT",
                "Timed out waiting for upstream capacity: local-main",
                retry_after_seconds=5.0,
            )

    clients = FakeGatewayClients()
    clients.main_llm = QueueFullClient(endpoint=RuntimeEndpoint("local-main", "http://main/v1", "local-main", 1))
    client = TestClient(create_gateway_app(settings(), clients))

    response = client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUEUE_TIMEOUT"
    assert response.headers["retry-after"] == "5"


def test_http_latency_histogram_covers_the_streaming_response_body():
    """streaming 지연은 헤더 시점이 아니라 본문이 끝난 시점까지여야 한다.

    metric 미들웨어가 BaseHTTPMiddleware였을 때 call_next는 응답 헤더가 정해지면
    돌아왔고, 1초짜리 SSE가 http_request_duration_seconds에 0.3ms로 기록됐다.
    """
    relay_seconds = 0.05

    class PacedClient(FakeRuntimeClient):
        async def open_stream(self, path, payload, **kwargs):
            return FakeUpstreamStream(self._paced())

        async def _paced(self):
            await asyncio.sleep(relay_seconds)
            yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

    clients = FakeGatewayClients()
    clients.main_llm = PacedClient(endpoint=RuntimeEndpoint("local-main", "http://main/v1", "local-main", 1))
    client = TestClient(create_gateway_app(settings(), clients))

    client.post(
        "/v1/chat/completions",
        headers=auth_headers(),
        json={"model": "local-main", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )

    metrics = client.get("/metrics", headers=auth_headers()).text
    observed = next(
        float(line.rsplit(" ", 1)[1])
        for line in metrics.splitlines()
        if line.startswith("http_request_duration_seconds_sum")
        and 'route="/v1/chat/completions"' in line
    )
    assert observed >= relay_seconds
