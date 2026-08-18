# Runtime patch lifecycle 관리

이 디렉터리는 runtime image 안에서 적용되는 임시 compatibility patch를 보관한다. patch는 Dockerfile inline 수정이 아니라 별도 script, metadata, verify 단계로 관리한다.

## `transformers_llama_head_dim_guard.py`

Kanana Prompt detector의 explicit Llama `head_dim` config가 일부 Transformers/vLLM 조합에서 config validation 단계에 막히는 문제를 우회한다.
현재 unified base는 Transformers 5.13.1과 huggingface_hub 1.23.0 조합을 정확히 검증한다.
과거 확인한 Transformers 하한 4.52.4는 호환성 이력일 뿐, exact pin보다 약한 빌드 입력으로
중복 관리하지 않는다.

## `apply_gemma4_multimodal_patches.py`

Gemma4-unified(12B) 모델의 이미지 FP8 오양자화, 오디오 warmup `fft_length` 누락을
고친다. `gemma4_unified` 코드 경로에만 걸려서 그 경로를 안 타는 다른 served
model에는 no-op이다.

두 수정 모두 upstream에서 해결돼 patch 없는 후보 이미지가 image/audio boot canary와 실제
vLLM smoke를 통과한 경우에만 제거한다. Kanana patch도 upstream이 explicit `head_dim`을
image patch 없이 허용하는 조합에서 같은 검증을 통과한 경우에만 제거한다.

## `apply_gemma4_streaming_reasoning_patch.py`

Pinned vLLM 0.25.1의 Gemma4 reasoning parser는 thinking을 켠 streaming 요청에서, model이
channel marker 없는 최종 답을 생성하면 이를 전부 `delta.reasoning`으로 분류하고 final
`content`를 비웠다. upstream PR #48262의 수정(2026-07-14 merge)을 backport한다. 새 model
turn은 content 상태에서 시작하고, 실제로 열린 `<|channel>` prompt만 reasoning 상태로
초기화한다.

이 patch는 Gemma4 parser 파일의 pre-fix method 전체를 anchor로 검증한다. upstream base가
수정을 포함하거나 레이아웃이 달라지면 build가 실패하므로, 그때 patch를 제거하고 parser
streaming canary를 다시 통과시킨다.

## 운영 원칙

- 2026-07-24부터 두 patch 모두 `ops/images/vllm-unified/Dockerfile` 하나에
  같이 적용된다(파일이 안 겹쳐 기계적으로 독립적임을 확인 후 병합) -- 더 이상
  `RISK_VLLM_IMAGE` 전용이 아니며, 26B/12B/embedding/embedding-ko/risk-prompt가
  전부 이 이미지를 쓴다.
- API path, request/response schema, model id, compose topology를 바꾸지 않는다.
- metadata JSON과 Docker label로 적용 사실을 증명한다.
- upstream 조합이 patch 없이 통과하면 그 patch만 제거한다(다른 patch는 그대로).

## 검증 명령

```bash
make build-vllm-unified-image
make risk-vllm-config-check
```

실제 image 검증은 `make risk-vllm-config-check`가 담당한다. patch 제거는 patch 없는 후보 image에서 이 검증과 실제 vLLM smoke를 통과한 경우에만 수행한다.
