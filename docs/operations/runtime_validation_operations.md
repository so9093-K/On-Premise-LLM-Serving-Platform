# 런타임 검증 운영 기준

이 문서는 full-stack runtime 검증 작업 순서와 산출물 기준을 정의한다. 운영 기준 문서이며, 과거 workplan이 아니다.

## 범위

- Gateway, Risk Adapter, enabled vLLM runtime(`main-llm-vllm`, `embedding-vllm`, `embedding-ko-vllm`, `risk-prompt-vllm`), Prometheus, Grafana, DCGM exporter, cAdvisor를 대상으로 한다.
- 검증은 target GPU host에서 수행한다.
- 보고서는 `reports/runtime/` 아래에 생성한다.
- live 검증은 `/v1/models`와 각 vLLM `/models`에서 `local-main`, `local-embed`, `local-embed-ko`, `risk-prompt` 노출을 확인한다.
- Gateway embedding canary는 `local-embed`와 `local-embed-ko`를 모두 호출한다. `local-embed-ko`는 1024차원 응답을 확인하고, retrieval query prefix 정책은 Gateway unit/contract test와 examples governance test에서 별도로 검증한다.

## 작업 순서

1. `HF_TOKEN=hf_xxx make first-run`으로 `.venv`, dependency, `.env`, validate, test, platform image, unified vLLM image, image 내부 Kanana config check를 한 번에 완료한다. 기존 `.env`에 토큰이 있으면 `HF_TOKEN=`은 생략한다.
2. `make preflight-compose`로 Docker, GPU 표시, host-published port, runtime secret, risk image config check를 확인한다.
3. `make compose-up`으로 full-stack을 기동한다.
4. `make ready-full`로 Gateway/Risk Adapter/vLLM readiness와 smoke를 확인한다.
5. `make runtime-validate`로 JSON/Markdown report를 작성한다.

## 실패 분류

- `risk-vllm-config-check` 실패: GPU, bitsandbytes, KV cache 이전의 image/HF loader 호환성 문제로 분류한다.
- `risk-vllm-config-check` 통과 후 vLLM serve 실패: vLLM runtime 구현, quantization load path, GPU memory allocation 순서로 분리한다.
- bnb ON/OFF 모두 같은 hidden/head validation 실패: quantization 문제가 아니라 config validation 문제로 분류한다.
- bnb OFF만 성공: bitsandbytes load path 또는 image dependency 조합 문제로 분류한다.

## 증빙

- `harness/runtime_validation_plan.md`의 Validation Report Rule을 따른다.
- runtime report는 raw prompt, user text, model output, token, secret을 기록하지 않는다.

## 설정 우선순위

`python scripts/validation/runtime_validation.py`는 live runtime 검증을 담당한다. 운영자가 후보 endpoint를 임시로 바꿔 검증할 수 있어야 하므로, host URL 계열 설정은 아래 우선순위를 따른다.

```text
CLI 인자 > process env / .env > built-in 기본값
```

```bash
# .env의 GATEWAY_BASE_URL보다 CLI 인자가 우선한다.
python scripts/validation/runtime_validation.py --gateway-base http://candidate-gateway:9400

# CLI 인자가 없으면 환경변수 또는 .env를 사용한다.
GATEWAY_BASE_URL=http://staging-gateway:9400 python scripts/validation/runtime_validation.py
```

| Runtime target | CLI 인자 | 환경변수 | 기본값 |
|---|---|---|---|
| Gateway | `--gateway-base` | `GATEWAY_BASE_URL` | `services.yaml`의 gateway `default_host_port` |
| Risk Adapter | `--risk-base` | `RISK_ADAPTER_BASE_URL` | `services.yaml`의 risk_adapter `default_host_port` |
| Main LLM vLLM | `--main-llm-base` | `MAIN_LLM_BASE_URL` | `services.yaml`의 main-llm-vllm `default_host_port` + `/v1` |
| Embedding vLLM | `--embedding-base` | `EMBEDDING_BASE_URL` | `services.yaml`의 embedding-vllm `default_host_port` + `/v1` |
| Embedding-ko vLLM | `--embedding-ko-base` | `EMBEDDING_KO_BASE_URL` | `services.yaml`의 embedding-ko-vllm `default_host_port` + `/v1` |
| Risk Prompt vLLM | `--risk-prompt-base` | `RISK_PROMPT_BASE_URL` | `services.yaml`의 risk-prompt-vllm `default_host_port` + `/v1` |
| Prometheus | `--prometheus-base` | `PROMETHEUS_BASE_URL` | `services.yaml`의 prometheus `default_host_port` |

`API_KEY`, `ADMIN_API_KEY`, `INTERNAL_SERVICE_TOKEN` 같은 secret은 명령 출력과 report에 노출하지 않는다.
