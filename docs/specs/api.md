# API 사양

Gateway는 외부 애플리케이션의 단일 진입점이다.

## 운영 endpoint

| endpoint | 인증 | 설명 |
|---|---|---|
| `GET /health` | 없음 | liveness |
| `GET /ready` | admin auth 또는 내부망 정책 | dependency readiness |
| `GET /metrics` | admin auth 또는 내부망 정책 | Prometheus metrics |
| `GET /docs` | 없음 | Scalar UI |
| `GET /redoc` | 없음 | ReDoc |
| `GET /openapi.json` | 없음 | OpenAPI JSON |

## 사용자 API

| endpoint | 설명 |
|---|---|
| `GET /v1/models` | 노출 모델 catalog. 로딩 상태는 필터링하지 않음 |
| `POST /v1/chat/completions` | `local-main` chat completion |
| `POST /v1/embeddings` | `local-embed` embedding |
| `POST /v1/risk/detectors/prompt/assessments` | prompt risk signal |
| `POST /v1/risk/detectors/siren/assessments` | siren risk signal |
| `POST /v1/risk/assessments` | aggregate risk signal |

사용자 API는 `Authorization: Bearer <API_KEY>`를 요구한다. admin endpoint가 보호되는 환경에서는 `Authorization: Bearer <ADMIN_API_KEY>`를 사용한다.

`/v1/models`는 “계약상 노출되는 모델 목록”을 반환한다. vLLM이 아직 로딩 중이어도 `local-main`, `local-embed`, `risk-prompt`, `risk-siren`은 catalog에 남는다. 현재 호출 가능한 상태인지 보려면 `/ready`의 `phase`와 `not_ready_dependencies`를 확인한다.

## 사용자 조정 가능 파라미터

`/v1/models` 응답의 각 model item은 `request_parameters`를 포함한다. 이 필드는 클라이언트가 문서나 schema를 따로 파싱하지 않고도 모델별 UI를 구성할 수 있도록, 사용자가 직접 조정할 수 있는 request parameter와 제약 조건을 노출한다.

| 모델 | 사용자 조정 가능 파라미터 | 비고 |
|---|---|---|
| `local-main` | `temperature`, `max_tokens`, `top_p`, `top_k`, `min_p`, `presence_penalty`, `frequency_penalty`, `repetition_penalty`, `stop`, `seed`, `n`, `tools`, `tool_choice`, `parallel_tool_calls`, `stream`, `stream_options` | `stream=true`는 SSE relay fast path로 지원하고 `stream_options.include_usage`는 `stream=true`와 함께 사용할 때 upstream이 지원하는 최종 usage chunk를 요청한다. `n`은 `1`만 허용. tool call은 Gemma4 parser 설정 범위에서만 허용 |
| `local-embed` | `dimensions`, `encoding_format`, `truncate_prompt_tokens` | `dimensions`는 `768`, `512`, `256`, `128` 중 하나. `encoding_format`은 `float`로 고정 |
| `risk-prompt` | 없음 | risk API는 `prompt`만 입력받고 detector parameter는 adapter가 `fixed_parameters`로 고정 |
| `risk-siren` | 없음 | risk API는 `prompt`만 입력받고 detector parameter는 adapter가 `fixed_parameters`로 고정 |

`request_parameters`는 prompt/messages/input 같은 필수 입력 본문을 뜻하지 않는다. 필수 입력은 각 request schema(`chat_completion_request`, `embedding_request`, `risk_assessment_request`)를 따른다. serving/runtime 하이퍼파라미터(`gpu_memory_utilization`, `max_model_len`, `max_num_seqs`, quantization 등)는 사용자 API에서 조정할 수 없고 운영자 config로만 변경한다.

상세 schema는 `specs/openapi.gateway.yaml`, `specs/openapi.risk-adapter.yaml`, `specs/schemas/*.json`을 기준으로 한다. Gateway/Risk Adapter의 generated OpenAPI는 `src/ai_model_serving/openapi_contracts.py`를 통해 동일한 checked-in JSON schema를 request/response body에 주입한다. 따라서 `/docs`와 `/openapi.json`에서 보이는 schema는 runtime contract validation과 같은 원천을 바라본다.

## Chat Streaming

`/v1/chat/completions`에 `stream: true`를 보내면 Gateway는 vLLM SSE chunk를 버퍼링 없이 `text/event-stream`으로 relay한다. 응답은 표준 OpenAI chunk 형태이며 마지막은 `data: [DONE]`으로 끝난다.

```bash
curl -sN http://localhost:9400/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"local-main","messages":[{"role":"user","content":"안녕"}],"stream":true}'
```

- curl에서 `-N`은 필수다. 없으면 curl이 chunk를 모았다가 한 번에 출력한다.
- `stream_options: {"include_usage": true}`를 함께 보내면 `[DONE]` 직전에 usage chunk가 추가된다.
- proxy(Nginx, Ingress) 앞단이 있으면 `proxy_buffering off` 설정이 필요하다. 상세는 `docs/operations/streaming_runtime_operations.md`를 참고한다.
- streaming 중 upstream 오류가 발생하면 정상 JSON error envelope 대신 `event: error` SSE event가 온다.

## Health/readiness 노출 제약

`/health`는 liveness 확인용으로 공개 가능하다. `/ready`와 `/metrics`는 admin auth 또는 internal network 보호가 필요하다. staging/production 성격의 환경에서는 외부 ingress에서 internal network 접근 정책을 함께 적용한다.
