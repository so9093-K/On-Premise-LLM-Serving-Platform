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
| `POST /v1/embeddings` | `local-embed` / `local-embed-ko` embeddings |
| `POST /v1/retrieval/rerank` | `local-embed-ko` / `local-embed` retrieval rerank |
| `POST /v1/retrieval/score` | `local-embed-ko` / `local-embed` retrieval score (입력 순서 유지) |
| `POST /v1/risk/detectors/prompt/assessments` | prompt risk signal |
| `POST /v1/risk/detectors/siren/assessments` | retired siren endpoint, 410 Gone |
| `POST /v1/risk/assessments` | aggregate risk signal |

사용자 API는 `Authorization: Bearer <API_KEY>`를 요구한다. admin endpoint가 보호되는 환경에서는 `Authorization: Bearer <ADMIN_API_KEY>`를 사용한다.

`/v1/models`는 “계약상 노출되는 모델 목록”을 반환한다. vLLM이 아직 로딩 중이어도 enabled public model인 `local-main`, `local-embed`, `local-embed-ko`, `risk-prompt`는 catalog에 남는다. 현재 호출 가능한 상태인지 보려면 `/ready`의 `phase`와 `not_ready_dependencies`를 확인한다.

## 사용자 조정 가능 파라미터

`/v1/models` 응답의 각 model item은 `request_parameters`를 포함한다. 이 필드는 클라이언트가 문서나 schema를 따로 파싱하지 않고도 모델별 UI를 구성할 수 있도록, 사용자가 직접 조정할 수 있는 request parameter와 제약 조건을 노출한다.

| 모델 | 사용자 조정 가능 파라미터 | 비고 |
|---|---|---|
| `local-main` | `temperature`, `max_tokens`, `top_p`, `top_k`, `min_p`, `presence_penalty`, `frequency_penalty`, `repetition_penalty`, `stop`, `seed`, `n`, `tools`, `tool_choice`, `parallel_tool_calls`, `stream`, `stream_options`, `reasoning`, `response_format`, `logprobs`, `top_logprobs`, `logit_bias` | `stream=true`는 SSE relay fast path로 지원하고 `stream_options.include_usage`는 `stream=true`와 함께 사용할 때 upstream이 지원하는 최종 usage chunk를 요청한다. `n`은 `1`만 허용. tool call은 Gemma4 parser 설정 범위에서만 허용. `reasoning=true`는 요청별 Gemma4 thinking opt-in이다 |
| `local-embed` | `dimensions`, `encoding_format`, `truncate_prompt_tokens` | `dimensions`는 `768`, `512`, `256`, `128` 중 하나. `encoding_format`은 `float`로 고정. token-array input과 base64 encoding_format은 smoke 미검증으로 지원하지 않는다. `user`는 embedding request schema에서 accept하지만 `/v1/models` `request_parameters` projection에는 포함하지 않는다. |
| `local-embed-ko` | `dimensions`, `encoding_format`, `truncate_prompt_tokens` | Korean dense retrieval 기본 모델. `dimensions`는 `1024` 고정이며 retrieval은 `dense_cosine`만 지원한다. `user`는 embedding request schema에서 accept하지만 `/v1/models` `request_parameters` projection에는 포함하지 않는다. |
| `risk-prompt` | 없음 | risk API는 `prompt`만 입력받고 detector parameter는 adapter가 `fixed_parameters`로 고정 |

Retrieval 기본 모델은 `local-embed-ko`다. `model`을 생략하면 `local-embed-ko`, `score_mode`를 생략하면 `dense_cosine`을 사용한다. 기존 late-interaction runtime은 유지보수 비용, token-level 응답 크기, 전용 artifact 운영 복잡도를 줄이기 위해 제거했다.

`/v1/retrieval/rerank`와 `/v1/retrieval/score`의 `truncate_prompt_tokens`는 내부 `/v1/embeddings` 호출에도 전달된다. vLLM OpenAI-compatible embedding request는 `truncate_prompt_tokens`를 받지만 `truncation_side`는 현재 baseline에서 확인된 embedding request parameter가 아니므로 retrieval schema에 포함하지 않는다. left/right truncation이 필요한 client는 tokenizer-aware pre-truncation 후 요청한다.

`request_parameters`는 prompt/messages/input 같은 필수 입력 본문을 뜻하지 않는다. 필수 입력은 각 request schema(`chat_completion_request`, `embedding_request`, `risk_assessment_request`)를 따른다. serving/runtime 하이퍼파라미터(`gpu_memory_utilization`, `max_model_len`, `max_num_seqs`, quantization 등)는 사용자 API에서 조정할 수 없고 운영자 config로만 변경한다. `local-main`의 RedHatAI FP8 Dynamic checkpoint는 model config의 `compressed-tensors` quantization metadata를 따르며, Gateway request parameter로 노출하지 않는다.

`local-main` 예시는 요청 예시일 뿐 Gateway가 기본 sampling 값을 주입한다는 뜻이 아니다. `temperature`, `max_tokens`, `top_p` 등을 생략하면 vLLM/OpenAI-compatible runtime 기본값을 따른다. 안정적인 smoke나 자동 검증에는 `max_tokens: 1`, `temperature: 0`, `n: 1`을 명시한다.

Chat API는 OpenAI 호환 chat completions의 bounded subset이다. `response_format`은 `text`, `json_object`, `json_schema`를 지원한다. `json_object`는 JSON mode라서 유효한 JSON만 확인하며 schema adherence는 보장하지 않는다. `json_object` 요청은 messages 안에 명시적인 JSON 지시문이 필요하다.

`json_schema`는 bounded OpenAI-compatible Structured Outputs subset을 사용한다. root schema는 object여야 하고 root `anyOf`는 거부하지만 nested `anyOf`는 project limit 안에서 허용한다. local `$defs`/`$ref`는 허용하며 recursive local `$ref`도 허용한다. external `$ref`는 허용하지 않으므로 `$ref` 값은 `#`로 시작하는 local reference여야 한다. 현재 advanced reference keyword인 `$dynamicRef`, `$recursiveRef`, `$dynamicAnchor`, `$recursiveAnchor`는 지원하지 않는다. `$id`와 `$anchor`도 local-only reference policy를 단순하게 유지하기 위해 지원하지 않는다. `$schema`는 JSON Schema draft annotation으로 허용될 수 있다. 모든 object schema는 `additionalProperties:false`를 설정해야 한다. 이 Gateway subset에서는 object의 모든 `properties`가 `required`에 포함되어야 하며, optional field는 `required`에서 빼는 대신 `"type": ["string", "null"]` 같은 nullable union으로 표현한다. `strict`는 OpenAI 호환성을 위해 허용하지만, Gateway의 schema 크기/깊이/속성 수/keyword safety limit은 `strict` 값과 무관하게 적용된다.

Unsupported keyword 제한은 schema object의 keyword에만 적용된다. JSON output property name에는 적용되지 않으므로 property 이름이 `$id`, `not`, `$dynamicRef` 같은 문자열이어도 `properties` map의 key로만 사용되면 허용된다. 반대로 property schema value 안에서 `$id`, `$dynamicRef`, `not` 등이 schema keyword로 사용되면 기존 정책대로 reject된다.

`top_logprobs`는 `logprobs=true`가 필요하며 Gateway 정책상 0..10으로 제한한다. OpenAI는 20까지 허용하지만 이 Gateway는 응답 크기와 latency 보호를 위해 10으로 cap한다. `logit_bias`는 token id string에서 bias number(-100..100)로 가는 object이며, token id는 OpenAI/tiktoken id가 아니라 served vLLM model tokenizer id로 해석한다.

`logit_bias`를 Structured Outputs 또는 tools와 같이 쓰는 것은 best-effort다. constrained decoding이나 tool protocol special token 처리가 token availability를 지배할 수 있다. `stream=true`와 `logprobs=true`는 SSE pass-through이며 clients가 chunk logprobs를 직접 파싱해야 한다. `json_schema + tools`, `json_schema + reasoning`은 전역 금지하지 않으며 runtime canary 결과에 따라 deployment가 완전 검증 여부를 광고하거나 operator config에서 특정 combination을 reject로 낮춘다. 현재 노출하지 않는 표준/확장 파라미터(`metadata` 등)는 Gateway allowlist에서 차단한다. `user` 필드는 `/v1/embeddings`에서 OpenAI API 호환용으로 accept하지만 metric label로 사용하지 않는다.

Tool calling을 사용할 때는 `tools`에 function tool을 포함하고 `tool_choice`를 `auto`, `required`, `none` 또는 제공된 function 이름으로 지정한다. `parallel_tool_calls`는 현재 `false`만 허용한다. Vision 요청은 bounded `data:image/*;base64,...` content part 1개만 허용하며 외부 이미지 URL fetch는 기본 차단이다. 이미지 크기 제한은 decoded bytes ≤ 7,000,000 (≈6.7 MB), 픽셀 수 ≤ 6,422,528 (Gemma 4 SigLIP2 최대 8타일 기준 상한, ≈2688×2394)이며, 허용 MIME type은 `image/jpeg`, `image/png`, `image/webp`다. 제한값 기준은 `configs/gpu_budgets.yaml`의 `main_llm_max_image_bytes` / `main_llm_max_image_pixels`이다. HTTP 요청 body 상한은 `operational_limits.max_request_body_bytes` (40,000,000 bytes)이며, base64 인코딩 오버헤드(×4/3)를 포함한 총 요청 크기가 이 한도를 초과하면 미들웨어에서 거부된다. 오디오가 배포된 active profile에서는 `input_audio` 1개를 허용하며, decoded audio는 ≤ 25,000,000 bytes이고 허용 format은 `wav`, `mp3`, `flac`, `ogg`다.

Reasoning/thinking은 기본값이 `false`다. 복잡한 디버깅·분석 요청에서만 `reasoning: true`를 명시하면 Gateway가 vLLM 전용 `chat_template_kwargs.enable_thinking=true`로 변환해 전달한다. 이 모드는 latency와 출력 토큰 사용량을 늘릴 수 있으며, 응답에는 runtime 버전에 따라 `message.reasoning` 또는 legacy `message.reasoning_content`가 포함될 수 있다. 최종 답변은 `message.content`를 사용한다.

상세 schema는 `specs/openapi.gateway.yaml`, `specs/openapi.risk-adapter.yaml`, `specs/schemas/*.json`을 기준으로 한다. Gateway/Risk Adapter의 generated OpenAPI는 `src/ai_model_serving/openapi_contracts.py`를 통해 동일한 checked-in JSON schema를 request/response body에 주입한다. 따라서 `/docs`와 `/openapi.json`에서 보이는 schema는 runtime contract validation과 같은 원천을 바라본다.

## Operation ID 목록

SDK/client generation을 위해 주요 route에 명시적 `operation_id`가 지정되어 있다.

| operation_id | 메서드 | 경로 | 서비스 |
|---|---|---|---|
| `getGatewayHealth` | GET | `/health` | Gateway |
| `getGatewayReadiness` | GET | `/ready` | Gateway |
| `getGatewayMetrics` | GET | `/metrics` | Gateway |
| `listModels` | GET | `/v1/models` | Gateway |
| `createChatCompletion` | POST | `/v1/chat/completions` | Gateway |
| `createEmbedding` | POST | `/v1/embeddings` | Gateway |
| `rerankDocuments` | POST | `/v1/retrieval/rerank` | Gateway |
| `scoreDocuments` | POST | `/v1/retrieval/score` | Gateway |
| `assessPromptRisk` | POST | `/v1/risk/detectors/prompt/assessments` | Gateway |
| `assessRetiredSirenRisk` | POST | `/v1/risk/detectors/siren/assessments` | Gateway |
| `assessRisk` | POST | `/v1/risk/assessments` | Gateway |
| `getRiskAdapterHealth` | GET | `/health` | Risk Adapter |
| `getRiskAdapterReadiness` | GET | `/ready` | Risk Adapter |
| `getRiskAdapterMetrics` | GET | `/metrics` | Risk Adapter |
| `assessRiskPromptDetector` | POST | `/v1/risk/detectors/prompt/assessments` | Risk Adapter |
| `assessRiskAggregate` | POST | `/v1/risk/assessments` | Risk Adapter |

`operation_id`는 전체 OpenAPI에서 unique해야 한다. route function 이름이나 path가 바뀌어도 SDK method name이 흔들리지 않도록 명시적으로 관리한다.

## Runtime Validation 정책

`make runtime-validate`는 현재 merge gate가 아니다. live vLLM runtime 환경 준비 후 별도 단계에서 실행한다.

config-only validation은 runtime 없이 실행 가능하다:
```bash
python scripts/validation/runtime_validation.py --config-only --allow-failures
```

live mode는 running stack이 필요하다. CI에서는 `make validate` (config/contract/docs validation만)를 사용한다.

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
- streaming 중 upstream 오류가 발생하면 정상 JSON error envelope 대신 `event: error` SSE event가 온다. Gateway stream guard가 chunk/byte limit을 초과한 경우도 같은 SSE error event로 전달되며 code는 `STREAM_LIMIT_EXCEEDED`다.

## 공통 Error Code

`CommonErrorResponse.error.code` enum은 `src/ai_model_serving/errors.py`의 `ERROR_STATUS`와 Gateway/Risk Adapter OpenAPI에 동시에 고정된다. `DETECTOR_DISABLED`는 Risk Adapter에서 detector가 설정되지 않았을 때 410으로 발생하며, Gateway가 Risk Adapter의 공통 error envelope를 받은 경우 410과 code를 보존한다.

## Health/readiness 노출 제약

`/health`는 liveness 확인용으로 공개 가능하다. `/ready`와 `/metrics`는 admin auth 또는 internal network 보호가 필요하다. staging/production 성격의 환경에서는 외부 ingress에서 internal network 접근 정책을 함께 적용한다.
