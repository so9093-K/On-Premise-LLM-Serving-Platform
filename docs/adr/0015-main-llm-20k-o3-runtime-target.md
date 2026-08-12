# ADR-0015: Main LLM 20K O3 Runtime Target

## Status

Superseded by ADR-0017 and ADR-0018

> **현재 운영 기준 (2026-08-12)**: 기본 Main Model profile은
> `configs/main_model_profiles.yaml`의 `gemma4-12b-unified-fp8`이며, 실제 serving limit은
> `configs/model_serving.yaml`이 정한다. 이 ADR의 26B / 20K / seq=1 내용은 당시의 검증과
> 실패·복구 근거를 보존하는 기록이며 현재 기본 runtime target이 아니다.

## Context

`local-main`은 RedHatAI의 preliminary FP8 Dynamic Gemma 4 26B-A4B checkpoint를 vLLM generation runtime으로 제공한다. Upstream model card는 B200/vLLM main 기준 96K context 예시를 제공하지만, 이 플랫폼의 기본 target GPU는 RTX 6000 Ada 48GB 단일 GPU이며 embedding, Korean retrieval embedding, prompt risk detector와 함께 여러 vLLM process가 동시에 상주한다.

기존 16K conservative policy는 long-context canary와 image/tool/structured-output 검증 범위를 제한했다. 동시에 `max_model_len`, `max_num_batched_tokens`, model card, catalog policy, compose command, generated reports가 분리되어 drift가 생기기 쉬운 구조였다.

`kv_cache_dtype: fp8_e5m2`는 higher-context runtime target 검토 과정에서 canary 후보로 올렸지만, 현재 main LLM checkpoint와 runtime image 조합에서는 `fp8_e5m2 kv-cache is not supported with fp8 checkpoints` 오류로 boot 단계에서 거부됐다. 따라서 이 값은 active runtime target에서 제외한다.

이후 `32K + O3` 조합은 같은 host budget에서 `To serve at least one request ... 6.88 GiB KV cache is needed ... available KV cache memory 2.72 GiB ... estimated maximum model length is 12928` 오류로 boot에 실패했다. 당시 active target은 known-good `16K + O2` baseline 위에서 상향한 `20K + O3 + gpu_memory_utilization 0.76`이었다.

## Decision

Main LLM runtime target을 다음 값으로 정의한다.

| Field | Value |
|---|---|
| `gpu_memory_utilization` | `0.76` |
| `max_model_len` | `20000` |
| `max_num_batched_tokens` | `20000` |
| `optimization_level` | `3` |

이 값은 production success claim이 아니라 20K context + optimization level 3 runtime target이다. Production claim은 target GPU에서 boot, startup/compile time, idle/peak VRAM, OOM/restart, KV cache usage ratio, prefix cache reuse/hit, TTFT, TBT/ITL, long-context, repeated-prefix, image input, structured output, tool calling, reasoning parser canary를 통과한 뒤 별도 evidence로 판단한다.

RedHatAI FP8 Dynamic checkpoint는 model config의 `compressed-tensors` quantization metadata를 따르므로 vLLM command에 `--quantization fp8`을 추가하지 않는다.

`optimization_level: 3`은 vLLM command의 `--optimization-level 3`으로 렌더링한다. `compilation_config`는 기본 target에는 추가하지 않는다. 향후 canary가 필요해 `compilation_config`를 쓰는 경우 현재 vLLM documentation 기준 JSON key는 `{"mode":3}` 형식을 사용하며, renderer는 compact JSON으로 렌더링한다.

Runtime 값을 수동 복제하지 않도록 `render_vllm_command()`와 compose validator는 option map과 ModelRegistry projection을 기준으로 `optimization_level`, optional `compilation_config` drift를 검증한다.

## Consequences

| Positive | Negative |
|---|---|
| 16K baseline보다 넓은 20K long-context, image, tool, structured-output canary를 같은 target policy로 검증할 수 있다 | RTX 6000 Ada 48GB에서 boot/compile time과 peak VRAM 실측 전까지 production 안정성을 주장할 수 없다 |
| Runtime option drift를 compose, model catalog, model card, serving config 사이에서 조기에 탐지한다 | 새 vLLM option 추가 시 validator field map을 함께 갱신해야 한다 |
| `--quantization fp8` 추가로 model config quantization metadata와 충돌하는 위험을 피한다 | Upstream preliminary checkpoint 변경 시 source facts와 canary를 재확인해야 한다 |
| unsupported `fp8_e5m2` KV cache 조합을 active policy에서 제거해 boot failure를 피한다 | KV cache dtype 실험은 별도 호환 image/model 조합에서 다시 검토해야 한다 |

## Operational impact

- 필수 static/config 검증: `make validate`, `modelctl validate/diff`, `validate_vllm_compose.py`, unit/contract tests, `render_vllm_commands.py --service main_llm`.
- 필수 GPU 검증: known-good baseline(`16K + O2`)과 active target(`20K + O3 + gpu_memory_utilization 0.76`)을 비교한다.
- Active target 실패 또는 품질/성능 회귀가 보일 때만 fallback diagnosis profile을 사용한다: `16K + O3`, `16K + O2`.
- Prefix caching 효과는 반복 prefix가 있는 요청에서 주로 기대하며, runtime report에서 reuse/hit 관련 지표를 별도 확인한다.

## Migration notes

- `configs/model_serving.yaml`, `configs/model_catalog.yaml`, `configs/gpu_budgets.yaml`, `model_cards/local-main.json`, `ops/compose/full-stack.private-network.yaml`를 같은 runtime target으로 정렬한다.
- `src/ai_model_serving/runtime_validation/vllm_commands.py`는 option map 기반 렌더링으로 확장한다.
- `scripts/compose/validate_vllm_compose.py`와 governance validation은 active runtime fields drift를 검사한다.
- JSON schema byte limit `16384`은 context length가 아니므로 변경하지 않는다.
- Timestamp가 붙은 과거 runtime validation report는 historical evidence로 남기고 소급 수정하지 않는다.

## Related

- [ADR-0011](0011-documentation-source-of-truth-policy.md) — 문서 Source-of-Truth와 Generated Block 정책
- [ADR-0014](0014-image-validation-policy.md) — Vision 이미지 검증 정책
- `configs/model_serving.yaml`
- `configs/model_catalog.yaml`
- `docs/resources/gpu_resource_requirements_48gb.md` — GPU 검증 이력

## Update (2026-07-16)

12B 프로필의 `--max-model-len`을 20K에서 50K로 올리는 과정에서(실제로 배포 서버에 전환해
검증 완료, `configs/main_model_profiles.yaml` 하단 `description` 참고), KV cache 용량을 실제로 좌우하는 게 `gpu_memory_utilization`만이 아니라
`--max-num-batched-tokens`이기도 하다는 걸 실측으로 확인했다.

- vLLM은 부팅 시 `--max-num-batched-tokens` 크기의 배치를 기준으로 activation 메모리를
  프로파일링한다. 이 값이 커지면 프로파일링이 필요로 하는 activation 메모리도 커지고,
  `gpu_memory_utilization` 예산은 고정이므로 그만큼 KV cache pool(= `num_gpu_blocks`)이
  줄어든다.
- 12B를 20K→50K로 올리면서 `--max-num-batched-tokens`도 같이 20000→50000으로 올렸더니,
  `num_gpu_blocks`가 20707→16638으로 줄었고, 그 결과 엔진 자체 VRAM 사용량도
  35.2GiB→30.2GiB로 줄었다(전체 GPU 사용량 41.6GiB→36.6GiB). 즉 context를 늘렸는데도
  실제 메모리 사용은 오히려 줄어드는 현상이 나타날 수 있다 -- 버그가 아니라 이 메커니즘의
  자연스러운 결과다.
- 같은 방식으로 26B(seq=1, 20K)도 재확인했다: `num_gpu_blocks=10812`
  (KV cache 172,992 tokens), 현재 설정의 최소 필요량(20,000 tokens) 대비 약 8.6배 여유.
  12B도 승격 전 20K/seq=2 기준으로 약 8배 여유가 있었다(`num_gpu_blocks=20707`,
  KV cache 331,312 tokens 대비 필요량 40,000 tokens). 두 모델 모두 기존 보수적인
  기본값에서 실제로는 상당한 여유를 갖고 있었다는 뜻이다.
- 실무적 시사점: `max_model_len`/`max_num_seqs`를 더 올릴 수 있는지는 이론 계산보다
  실제 `/metrics`의 `vllm:cache_config_info{num_gpu_blocks=...}`를 부팅 후 직접 읽는 게
  가장 신뢰도 높은 확인 방법이다. `configs/gpu_budgets.yaml`의 `live_tuning_order`
  정책(측정 먼저, 이후 조정)과 일치한다.
