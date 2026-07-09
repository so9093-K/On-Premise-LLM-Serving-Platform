# Streaming Runtime Operations

`/v1/chat/completions`는 `stream=true` 요청을 공식 contract로 허용한다. Gateway는 요청 schema와 모델별 parameter policy를 먼저 검증한 뒤 vLLM의 SSE 응답을 `text/event-stream`으로 relay한다. 이 경로는 전체 응답 JSON을 모아서 검증하지 않는 fast path이므로 운영 정책도 non-stream과 다르다.


## Standard OpenAI-compatible chunk shape

Gateway는 upstream SSE event를 재구성하지 않는다. 정상 chunk는 OpenAI Chat Completions streaming event와 같은 형태를 기대한다.

```text
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1694268190,"model":"local-main","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1694268190,"model":"local-main","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1694268190,"model":"local-main","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

`stream=true`와 함께 `stream_options.include_usage=true`를 보낸 경우 upstream이 지원하면 `[DONE]` 직전에 `choices: []`와 전체 `usage`를 포함한 chunk가 추가될 수 있다. stream이 중단되거나 cancel되면 이 최종 usage chunk를 받지 못할 수 있으므로 billing/accounting의 source of truth는 upstream 관측값과 함께 검증한다.

`stream=true`와 `logprobs=true`는 Gateway에서 허용한다. 이 경로는 pass-through SSE이므로 Gateway가 chunk-level logprobs shape를 전체 검증하지 않는다. client는 각 `chat.completion.chunk`의 `choices[].logprobs`를 직접 파싱해야 하며, non-stream 응답에서만 Gateway가 `choices[].logprobs` 위치와 token logprob item shape를 검증한다.

Structured Outputs 관련 조합도 streaming 정책과 분리해서 본다. `json_schema + tools`, `json_schema + reasoning`은 Gateway에서 전역 금지하지 않고, live canary가 deployment별 지원 여부를 확인한다. canary 실패는 runtime report의 degraded feature로 기록되며 운영자가 해당 deployment에서만 combination policy를 `reject`로 낮출 수 있다.

## Proxy buffering

Gateway 응답에는 다음 header를 항상 포함한다.

```http
Cache-Control: no-cache
X-Accel-Buffering: no
Content-Type: text/event-stream
```

Nginx 또는 Ingress가 앞단에 있으면 proxy buffering도 꺼야 한다. 그렇지 않으면 vLLM과 Gateway가 chunk를 생성해도 proxy가 chunk를 모았다가 한 번에 내려보낼 수 있다.

```nginx
location /v1/chat/completions {
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 180s;
    proxy_send_timeout 180s;
}
```

Kubernetes Ingress를 쓴다면 controller별 annotation으로 buffering과 idle timeout을 확인한다. 예를 들어 NGINX Ingress 계열은 `nginx.ingress.kubernetes.io/proxy-buffering: "off"`와 충분한 `proxy-read-timeout`을 설정한다.

## Streaming error policy

Streaming은 response header가 이미 전송된 뒤 upstream timeout, upstream connection reset, client disconnect가 발생할 수 있다. 이 시점에는 정상 JSON error envelope로 전환할 수 없다.

Gateway 정책은 다음과 같다.

| 상황 | Gateway 동작 |
|---|---|
| Request validation 실패 | 일반 JSON error envelope, HTTP 422 |
| Queue/circuit/upstream 오류가 streaming transport 선택 후 발생 | SSE `event: error` + `data: [DONE]` |
| 일부 chunk relay 후 upstream 오류 발생 | 이미 보낸 chunk는 유지하고 SSE `event: error` + `data: [DONE]` |
| Client disconnect | upstream stream을 중단하고 `streaming_errors_total{code="CLIENT_DISCONNECT"}` metric 기록 |

SSE error event 예시는 다음과 같다.

```text
event: error
data: {"error":{"code":"UPSTREAM_TIMEOUT","message":"...","retryable":true,"request_id":"req_..."}}

data: [DONE]
```

클라이언트는 `event: error`를 만나면 현재 partial text를 완료된 응답으로 간주하지 말고 retry 또는 사용자 안내 정책을 적용해야 한다.

## Usage accounting

Gateway는 streaming chunk를 수정하지 않고 그대로 relay한다. 단, `data:` line 안에 JSON 객체가 있고 그 객체에 non-null `usage` field가 있으면 usage accounting event가 있었다는 사실만 metric으로 센다. OpenAI-compatible 사용량 chunk를 원하면 요청에 `stream: true`와 `stream_options: {"include_usage": true}`를 함께 포함한다.

수집 metric:

```text
streaming_chunks_total{service="gateway",target="local-main"}
streaming_bytes_total{service="gateway",target="local-main"}
streaming_usage_events_total{service="gateway",target="local-main"}
streaming_errors_total{service="gateway",target="local-main",code="...",phase="..."}
streaming_requests_total{service="gateway",target="local-main",status="started"}
streaming_time_to_first_chunk_seconds_bucket{service="gateway",target="local-main",le="..."}
streaming_duration_seconds_bucket{service="gateway",target="local-main",status="completed",le="..."}
streaming_chunks_per_response_bucket{service="gateway",target="local-main",status="completed",le="..."}
streaming_client_disconnects_total{service="gateway",target="local-main",phase="before_first_chunk|mid_stream"}
```

주의: prompt, generated text, token delta, usage 숫자는 metric label로 내보내지 않는다. 사용량의 source of truth가 필요하면 vLLM final usage chunk 또는 upstream observability를 함께 확인한다.

## Timeout tuning

Streaming은 일반 JSON 응답보다 HTTP 연결을 오래 유지한다. 다음 timeout을 함께 맞춘다.

| 계층 | 설정 | 권장 원칙 |
|---|---|---|
| Gateway admission | `*_QUEUE_TIMEOUT_SECONDS` | 정상 동시 요청은 즉시 실패시키지 않도록 main LLM은 운영 대기 시간을 둔다. 보조/분류 runtime은 짧게 유지해도 된다. |
| vLLM read timeout | `*_TIMEOUT_SECONDS` / model `timeout_seconds` | 토큰 생성 중 idle gap을 견딜 수 있게 설정 |
| Gateway global timeout | `REQUEST_TIMEOUT_SECONDS` | non-stream JSON request budget. streaming은 upstream read timeout과 proxy idle timeout이 더 중요 |
| Reverse proxy idle timeout | Ingress/Nginx read timeout | 최장 생성 시간보다 길게 설정 |
| Client timeout | fetch/requests timeout | streaming read loop가 idle timeout을 별도로 갖도록 설정 |

장문 생성이 많으면 `main_llm.timeout_seconds`, proxy read timeout, client read timeout을 같이 늘린다. Main LLM queue timeout은 사용자 요청을 vLLM scheduler queue로 넘길 수 있을 만큼 확보하되, 무제한 대기는 피한다. 보조 runtime의 queue timeout은 빠른 실패와 재시도 정책에 맞춰 짧게 유지한다.

## 운영 smoke check

```bash
curl -N \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  http://localhost:9400/v1/chat/completions \
  -d '{"model":"local-main","messages":[{"role":"user","content":"스트리밍 상태를 한 문장으로 설명해줘"}],"stream":true}'
```

확인할 것:

1. 첫 `data:` line이 전체 답변 완료 전 도착한다.
2. 마지막에 `data: [DONE]`이 온다.
3. proxy를 거친 경로에서도 chunk가 한 번에 뭉쳐 오지 않는다.
4. `/metrics`에 streaming chunk/byte/usage/error metric이 노출된다.


## Runtime validation coverage

`python scripts/validation/runtime_validation.py`의 live mode는 non-stream chat/embedding smoke와 별도로 streaming chat smoke를 수행한다. 이 check는 `text/event-stream` content type, 첫 `data:` chunk 도착 시간, `[DONE]` 수신 여부를 확인한다. live mode는 또한 Grafana `/api/health`, Prometheus datasource, dashboard UID import 상태를 확인한다. 브라우저 screenshot 수준의 render 확인은 아직 선택적 후속 검증 항목이며, proxy buffering 감지는 운영 ingress 환경에서 별도 확인한다.
