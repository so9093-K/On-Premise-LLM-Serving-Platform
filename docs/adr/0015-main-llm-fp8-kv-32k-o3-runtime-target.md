# ADR-0015: Main LLM FP8 KV 32K O3 Runtime Target

## Status

Accepted

## Context

`local-main`은 RedHatAI의 preliminary FP8 Dynamic Gemma 4 26B-A4B checkpoint를 vLLM generation runtime으로 제공한다. Upstream model card는 B200/vLLM main 기준 96K context 예시를 제공하지만, 이 플랫폼의 기본 target GPU는 RTX 6000 Ada 48GB 단일 GPU이며 embedding, Korean retrieval embedding, prompt risk detector와 함께 여러 vLLM process가 동시에 상주한다.

기존 16K conservative policy는 long-context canary와 image/tool/structured-output 검증 범위를 제한했다. 동시에 `max_model_len`, `max_num_batched_tokens`, model card, catalog policy, compose command, generated reports가 분리되어 drift가 생기기 쉬운 구조였다.

## Decision

Main LLM runtime target을 다음 값으로 정의한다.

| Field | Value |
|---|---|
| `max_model_len` | `32768` |
| `max_num_batched_tokens` | `32768` |
| `kv_cache_dtype` | `fp8_e5m2` |
| `optimization_level` | `3` |

이 값은 production success claim이 아니라 FP8 KV 기반 32K runtime target이다. Production claim은 target GPU에서 boot, startup/compile time, idle/peak VRAM, OOM/restart, KV cache usage ratio, prefix cache reuse/hit, TTFT, TBT/ITL, long-context, repeated-prefix, image input, structured output, tool calling, reasoning parser canary를 통과한 뒤 별도 evidence로 판단한다.

RedHatAI FP8 Dynamic checkpoint는 model config의 `compressed-tensors` quantization metadata를 따르므로 vLLM command에 `--quantization fp8`을 추가하지 않는다.

`optimization_level: 3`은 vLLM command의 `--optimization-level 3`으로 렌더링한다. `compilation_config`는 기본 target에는 추가하지 않는다. 향후 canary가 필요해 `compilation_config`를 쓰는 경우 현재 vLLM documentation 기준 JSON key는 `{"mode":3}` 형식을 사용하며, renderer는 compact JSON으로 렌더링한다.

Runtime 값을 수동 복제하지 않도록 `render_vllm_command()`와 compose validator는 option map과 ModelRegistry projection을 기준으로 `kv_cache_dtype`, `optimization_level`, optional `compilation_config` drift를 검증한다.

## Consequences

| Positive | Negative |
|---|---|
| 32K long-context, image, tool, structured-output canary를 같은 target policy로 검증할 수 있다 | RTX 6000 Ada 48GB에서 boot/compile time과 peak VRAM 실측 전까지 production 안정성을 주장할 수 없다 |
| FP8 KV cache로 long-context KV cache memory footprint를 줄일 수 있다 | FP8 KV는 weight, CUDA context, activation/workspace, fragmentation까지 줄이는 전체 VRAM 50% 감소가 아니다 |
| Runtime option drift를 compose, model catalog, model card, serving config 사이에서 조기에 탐지한다 | 새 vLLM option 추가 시 validator field map을 함께 갱신해야 한다 |
| `--quantization fp8` 추가로 model config quantization metadata와 충돌하는 위험을 피한다 | Upstream preliminary checkpoint 변경 시 source facts와 canary를 재확인해야 한다 |

## Operational impact

- 필수 static/config 검증: `make validate`, `modelctl validate/diff`, `validate_vllm_compose.py`, `runtime_validation.py --config-only`, unit/contract tests, `render_vllm_commands.py --service main_llm`.
- 필수 GPU 검증: current baseline과 final canary(`32K + FP8 KV + O3`)를 비교한다.
- Final canary 실패 또는 품질/성능 회귀가 보일 때만 fallback diagnosis profile을 사용한다: `32K + FP8 KV + O2`, `16K + FP8 KV + O2`, `16K + 기존 KV auto + O2`.
- Prefix caching 효과는 반복 prefix가 있는 요청에서 주로 기대하며, runtime report에서 reuse/hit 관련 지표를 별도 확인한다.

## Migration notes

- `configs/model_serving.yaml`, `configs/model_catalog.yaml`, `configs/gpu_budgets.yaml`, `model_cards/local-main.json`, `ops/compose/full-stack.private-network.yaml`를 같은 runtime target으로 정렬한다.
- `src/ai_model_serving/runtime_validation/vllm_commands.py`는 option map 기반 렌더링으로 확장한다.
- `scripts/compose/validate_vllm_compose.py`와 governance validation은 new runtime fields drift를 검사한다.
- JSON schema byte limit `16384`은 context length가 아니므로 변경하지 않는다.
- Timestamp가 붙은 과거 runtime validation report는 historical evidence로 남기고 소급 수정하지 않는다.

## Related

- [ADR-0011](0011-documentation-source-of-truth-policy.md) — 문서 Source-of-Truth와 Generated Block 정책
- [ADR-0014](0014-image-validation-policy.md) — Vision 이미지 검증 정책
- `configs/model_serving.yaml`
- `configs/model_catalog.yaml`
- `docs/resources/gpu_resource_requirements_48gb.md`
- `docs/resources/gpu_resource_plan.md`
