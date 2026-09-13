<!-- 이 파일은 scripts/render_runtime_assets.py가 생성한다. 직접 고치지 않는다.
     내용의 출처는 specs/schemas/chat_completion_{request,response}.schema.json이다. -->

# OpenAI 호환 범위

이 Gateway는 OpenAI Chat Completions의 **한정된 부분집합**을 구현하고, 거기에
플랫폼 확장을 더한다. 기존 OpenAI SDK로 `base_url`만 바꿔 호출할 수 있지만
받는 파라미터는 아래 표가 전부다. 선언되지 않은 파라미터는 조용히 무시하지
않고 422로 거부한다 -- 무시하면 사용자가 적용됐다고 믿기 때문이다.

요청 파라미터 25개 가운데 표준 15개, 좁힌 표준 6개, 확장 4개다.

## 요청 파라미터

| 파라미터 | 분류 | 제약 | 비고 |
|---|---|---|---|
| `min_p` | 확장 | number, 최소 0, 최대 1 | vLLM/MLX runtime의 sampling 파라미터다. |
| `reasoning` | 확장 | boolean | boolean opt-in이다. OpenAI의 reasoning_effort와 다른 필드다. |
| `repetition_penalty` | 확장 | number, 초과 0, 최대 2 | vLLM/MLX runtime의 sampling 파라미터다. |
| `top_k` | 확장 | integer, 최소 -1 | vLLM/MLX runtime의 sampling 파라미터다. |
| `logit_bias` | 표준(좁힘) | object | token id를 서빙 중인 모델의 tokenizer로 해석한다. OpenAI tokenizer가 아니다. |
| `model` (필수) | 표준(좁힘) | - | 활성 프로필이 선언한 이름 하나만 받는다. |
| `n` | 표준(좁힘) | integer, 최소 1, 최대 1 | 1만 받는다. 여러 후보 생성은 지원하지 않는다. |
| `stream_options` | 표준(좁힘) | object | include_usage만 받는다. include_obfuscation을 포함한 나머지는 거부한다. |
| `top_logprobs` | 표준(좁힘) | integer, 최소 0, 최대 10 | OpenAI는 20까지 허용하지만 이 Gateway는 10으로 제한한다. |
| `user` | 표준(좁힘) | string | 형식만 검증하고 upstream에 전달하지 않는다. |
| `frequency_penalty` | 표준 | number, 최소 -2, 최대 2 | - |
| `logprobs` | 표준 | boolean | - |
| `max_completion_tokens` | 표준 | integer, 최소 1, 최대 13000 | `max_tokens`와 같은 한도를 가리키는 OpenAI 표준 이름이다. 둘을 함께 보내면 거부한다. |
| `max_tokens` | 표준 | integer, 최소 1, 최대 13000 | - |
| `messages` (필수) | 표준 | array | - |
| `parallel_tool_calls` | 표준 | boolean | - |
| `presence_penalty` | 표준 | number, 최소 -2, 최대 2 | - |
| `response_format` | 표준 | object | - |
| `seed` | 표준 | integer, 최소 0 | - |
| `stop` | 표준 | - | - |
| `stream` | 표준 | boolean | When true, Gateway relays the upstream SSE response as text/event-stream without buffering. Each chat.completion.chunk is narrowed to the fields this contract declares, so runtime-internal state never reaches the client. |
| `temperature` | 표준 | number, 최소 0, 최대 2 | - |
| `tool_choice` | 표준 | - | - |
| `tools` | 표준 | array | - |
| `top_p` | 표준 | number, 초과 0, 최대 1 | - |

## 거부하는 OpenAI 파라미터

아래는 OpenAI가 문서화했지만 이 플랫폼이 받지 않는 것들이다. 보내면 422다.

| 파라미터 | 이유 |
|---|---|
| `service_tier` | 단일 온프레미스 runtime이라 처리 등급 선택이 없다. |
| `store` | 플랫폼이 대화를 저장하지 않는다. |
| `metadata` | 저장하지 않으므로 붙일 대상이 없다. |
| `reasoning_effort` | 이 플랫폼은 boolean reasoning opt-in을 쓴다. |
| `modalities` | 출력 modality는 활성 프로필이 결정한다. |
| `audio` | 음성 출력 runtime을 서빙하지 않는다. |
| `prediction` | predicted outputs를 지원하는 runtime이 없다. |
| `web_search_options` | 외부 egress가 없는 배포라 웹 검색을 제공하지 않는다. |
| `functions` | OpenAI가 폐기한 이름이다. tools를 쓴다. |
| `function_call` | OpenAI가 폐기한 이름이다. tool_choice를 쓴다. |
| `stream_options.include_obfuscation` | SSE 패딩을 넣지 않는다. |

## 응답 메시지 필드

응답은 아래 필드로 좁혀서 나간다. runtime이 덧붙인 내부 상태는 전달하지 않는다.

| 파라미터 | 분류 | 제약 | 비고 |
|---|---|---|---|
| `reasoning` | 확장 | string/null | OpenAI 응답에는 없는 필드다. 요청이 reasoning=true일 때만 실린다. |
| `reasoning_content` | 확장 | string/null | OpenAI 응답에는 없는 필드다. runtime의 reasoning parser가 분리한 원문이다. |
| `content` | 표준 | string/null | - |
| `refusal` | 표준 | string/null | - |
| `role` (필수) | 표준 | - | - |
| `tool_calls` | 표준 | array | - |
