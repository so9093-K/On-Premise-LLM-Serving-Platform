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
        "stream": {"type": "boolean"}
      }
    }
  ]
}
```

## 모델별 정책

| 모델 | 사용자 조정 가능 parameter | 설명 |
|---|---|---|
| `local-main` | `temperature`, `max_tokens`, `top_p`, `top_k`, `min_p`, `presence_penalty`, `frequency_penalty`, `repetition_penalty`, `stop`, `seed`, `tools`, `tool_choice`, `parallel_tool_calls`, `stream`, `stream_options` | Chat/sampling/tool 관련 값만 Gateway contract 범위에서 조정한다. `n`은 최대값이 1로 고정되므로 클라이언트 UI에 노출하지 않는다. `stream=true`는 Gateway streaming fast path로 SSE를 실시간 relay한다. `stream_options.include_usage=true`는 `stream=true`와 함께 사용할 때 upstream이 지원하는 OpenAI-compatible final usage chunk를 그대로 전달한다. |
| `local-embed` | `dimensions`, `encoding_format`, `truncate_prompt_tokens` | embedding dimension과 prompt truncation 범위만 조정한다. |
| `risk-prompt` | 없음 | risk endpoint는 `prompt` 입력만 받는다. detector sampling 값은 adapter가 고정한다. |

## 클라이언트 UI 권장 흐름

1. `/v1/models`를 호출한다.
2. 사용자가 선택한 model item의 `capabilities`로 화면 종류를 결정한다.
3. `request_parameters`의 각 constraint를 입력 form으로 변환한다.
4. `fixed_parameters`는 읽기 전용 설명으로만 표시하거나 숨긴다.
5. 호출 전 request schema와 동일한 제한을 클라이언트에서도 한 번 더 검증한다.

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
