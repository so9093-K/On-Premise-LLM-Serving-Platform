# Runtime patch lifecycle 관리

이 디렉터리는 runtime image 안에서 적용되는 임시 compatibility patch를 보관한다. patch는 Dockerfile inline 수정이 아니라 별도 script, metadata, verify 단계로 관리한다.

## `transformers_llama_head_dim_guard.py`

Kanana Prompt detector의 explicit Llama `head_dim` config가 일부 Transformers/vLLM 조합에서 config validation 단계에 막히는 문제를 우회한다.

## 운영 원칙

- patch는 `RISK_VLLM_IMAGE` 전용이다.
- main Gemma4 runtime image에는 적용하지 않는다.
- API path, request/response schema, model id, compose topology를 바꾸지 않는다.
- metadata JSON과 Docker label로 적용 사실을 증명한다.
- upstream 조합이 patch 없이 통과하면 제거한다.

## 검증 명령

```bash
make build-risk-vllm-image
make risk-vllm-config-check
```

자세한 운영 문서는 `docs/operations/risk_vllm_patch_lifecycle.md`를 따른다.
