# 모델별 사용자 조정 가능 파라미터

이 문서는 사용자 API에서 조정할 수 있는 parameter와 운영자가 config로만 바꿀 수 있는 runtime 하이퍼파라미터를 구분한다.

## 원칙

- 클라이언트는 `/v1/models`를 호출해 모델별 `capabilities`와 `request_parameters`를 확인한다.
- `request_parameters`는 사용자가 request body에서 직접 조정할 수 있는 값만 포함한다.
- `messages`, `input`, `prompt` 같은 필수 입력 본문은 각 request schema의 필수 필드이며, 조정 가능 하이퍼파라미터가 아니므로 `request_parameters`에 넣지 않는다.
- `fixed_parameters`는 adapter/runtime이 내부적으로 고정하는 값이다. 사용자 form에는 노출하지 않는다.
- `max_model_len`, `max_num_seqs`, `gpu_memory_utilization`, quantization, runner, dtype 같은 serving/runtime 하이퍼파라미터는 사용자 API에서 조정하지 않는다. 운영자가 `configs/model_serving.yaml`과 catalog/model card를 함께 변경한다.

## `/v1/models` 응답 예시

```json
{
  "object": "list",
  "data": [
    {
      "id": "local-main",
      "object": "model",
      "backend": "main_llm_vllm",
      "capabilities": ["chat.completions", "chat.completions.vision", "chat.completions.tools"],
      "request_parameters": {
        "temperature": {"type": "number", "min": 0, "max": 2},
        "max_tokens": {"type": "integer", "min": 1, "max": 1024},
        "top_p": {"type": "number", "min_exclusive": 0, "max": 1},
        "top_k": {"type": "integer", "min": -1},
        "min_p": {"type": "number", "min": 0, "max": 1},
        "seed": {"type": "integer", "min": 0},
        "n": {"type": "integer", "min": 1, "max": 1},
        "stream": {"type": "boolean"}
      }
    }
  ]
}
```

## 모델별 정책

| 모델 | 사용자 조정 가능 parameter | 설명 |
|---|---|---|
| `local-main` | `temperature`, `max_tokens`, `top_p`, `top_k`, `min_p`, `presence_penalty`, `frequency_penalty`, `repetition_penalty`, `stop`, `seed`, `n`, `tools`, `tool_choice`, `parallel_tool_calls`, `stream`, `stream_options`, `response_format`, `logprobs`, `top_logprobs`, `logit_bias` | Chat/sampling/tool/structured output 관련 값만 Gateway contract 범위에서 조정한다. `n`은 OpenAI client 호환을 위해 `1`만 허용하며 UI에서는 숨기거나 읽기 전용으로 표시한다. `stream=true`는 Gateway streaming fast path로 SSE를 실시간 relay한다. `stream_options.include_usage=true`는 `stream=true`와 함께 사용할 때 upstream이 지원하는 OpenAI-compatible final usage chunk를 그대로 전달한다. |
| `local-embed` | `dimensions`, `encoding_format`, `truncate_prompt_tokens` | embedding dimension과 prompt truncation 범위만 조정한다. |
| `risk-prompt` | 없음 | risk endpoint는 `prompt` 입력만 받는다. detector sampling 값은 adapter가 고정한다. |

## 클라이언트 UI 권장 흐름

1. `/v1/models`를 호출한다.
2. 사용자가 선택한 model item의 `capabilities`로 화면 종류를 결정한다.
3. `request_parameters`의 각 constraint를 입력 form으로 변환한다.
4. `fixed_parameters`는 읽기 전용 설명으로만 표시하거나 숨긴다.
5. 호출 전 request schema와 동일한 제한을 클라이언트에서도 한 번 더 검증한다.

`request_parameters`는 허용 표면과 제약 조건이지, Gateway가 주입하는 기본값 목록이 아니다. Chat UI가 기본 입력값을 정해야 한다면 일반 대화는 `temperature=0.7`, `top_p=0.9`, `max_tokens=512` 같은 client preset으로 두고, smoke/debug preset은 `temperature=0`, `max_tokens=1`, `n=1`로 분리한다. `reasoning`은 기본 `false`로 두고 분석·디버깅 preset에서만 켠다. `parallel_tool_calls`는 `false` 고정이므로 toggle로 노출하지 않는다. Vision 입력은 base64 `data:image` 1개만 허용하므로 외부 URL 업로드 UX는 별도 proxy/egress 정책이 생기기 전까지 제공하지 않는다.

`response_format.allowed_types`는 `text`, `json_object`, `json_schema`다. `json_object`는 JSON mode이고 schema adherence를 보장하지 않으므로 UI는 별도 schema 입력란을 열지 않는다. `json_object`는 messages 안의 명시적인 JSON 지시문이 필요하다. `json_schema`는 bounded OpenAI-compatible Structured Outputs subset이며 root object와 `additionalProperties:false`를 요구한다. root `anyOf`는 거부하지만 nested `anyOf`는 limit 안에서 허용한다. local `$defs`/`$ref`와 recursive local `$ref`는 허용하지만 external `$ref`는 허용하지 않는다. `$ref` 값은 `#`로 시작하는 local reference여야 한다. Phase 1에서는 `$dynamicRef`, `$recursiveRef`, `$dynamicAnchor`, `$recursiveAnchor`를 지원하지 않고, `$id`와 `$anchor`도 local-only reference policy를 단순하게 유지하기 위해 지원하지 않는다. `$schema`는 JSON Schema draft annotation으로 허용될 수 있다. 모든 object property는 `required`에 포함되어야 하며 optional처럼 보이는 field는 `"type": ["string", "null"]` nullable union으로 표현한다. `strict`는 OpenAI 호환성을 위해 받지만 Gateway safety limit은 항상 적용된다. `top_logprobs`는 `logprobs=true`가 필요하고 Gateway cap은 10이다(OpenAI 표준은 20까지 허용). `logit_bias.token_id_semantics`가 `served_model_tokenizer`이면 OpenAI/tiktoken id가 아니라 serving 중인 vLLM tokenizer id를 입력해야 한다.

Unsupported keyword 제한은 schema object keyword에만 적용되고 JSON output property name에는 적용되지 않는다. 따라서 client UI는 `$id`, `not`, `$dynamicRef` 같은 문자열도 output field name으로 허용할 수 있다. 다만 그 문자열이 property schema value 안에서 schema keyword로 사용되면 Gateway가 reject한다.

`json_schema + tools`, `json_schema + reasoning`은 discovery surface에서 전역 금지하지 않는다. `capability_gate`는 request validator가 기본 허용하고 live canary가 조합 지원 여부를 검증한다는 의미다. canary 결과가 degraded이면 runtime report에 degraded feature로 남고, 운영자가 해당 deployment에서만 combination policy를 `reject`로 낮출 수 있다. client는 `/v1/models`와 운영 readiness/report를 함께 읽어 고급 조합을 노출한다. `logit_bias`와 Structured Outputs/tools 조합은 constrained decoding이나 tool protocol이 token bias보다 우선할 수 있어 best-effort로 설명한다.

## UI parameter grouping 권장

클라이언트가 `request_parameters`를 form으로 표시할 때 다음 grouping을 권장한다.

| 그룹 | parameter |
|---|---|
| Basic generation | `max_tokens`, `temperature`, `top_p` |
| Advanced sampling | `top_k`, `min_p`, `presence_penalty`, `frequency_penalty`, `repetition_penalty`, `seed`, `n` |
| Streaming | `stream`, `stream_options` |
| Tools | `tools`, `tool_choice`, `parallel_tool_calls` |
| Structured Outputs | `response_format` |
| Diagnostics | `logprobs`, `top_logprobs` |
| Advanced token control | `logit_bias` (`logit_bias` token id는 served model tokenizer 기준 — OpenAI/tiktoken id와 다름) |
| Vision | `image_url` content part |

`logit_bias`는 served vLLM model tokenizer token id를 사용해야 하므로 Advanced 섹션에 설명을 포함하고 기본적으로 숨긴다. `/v1/models`에 `request_parameter_groups` 같은 새 field를 추가하는 것은 별도 PR로 진행한다.

## 운영자 변경 절차

사용자 조정 가능 parameter를 추가하거나 제거할 때는 다음 파일이 함께 맞아야 한다.

- `configs/model_serving.yaml`의 `request_parameter_policy`
- `src/ai_model_serving/contracts/*`의 request validator
- `src/ai_model_serving/domain/model_registry.py`의 `/v1/models` projection
- `specs/schemas/*_request.schema.json`
- `specs/schemas/model_list_response.schema.json`
- `specs/openapi.gateway.yaml`
- 관련 unit/contract test

변경 후 최소 검증:

```bash
python scripts/validation/validate_contracts.py
python scripts/validation/openapi_snapshot_diff.py
python scripts/models/modelctl.py validate
python scripts/models/modelctl.py diff
python scripts/validation/run_tests.py -q
python scripts/validation/release_check.py --step-timeout-seconds 60
```
