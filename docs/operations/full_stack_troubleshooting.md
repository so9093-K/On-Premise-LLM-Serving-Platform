# Full-stack 문제 해결 가이드

이 문서는 `make compose-up` / `make ready-full`에서 막혔을 때 임시로 옵션을 제거하는 방식이 아니라, 원인별로 확인하고 수정하는 절차를 정의한다.

## 기본 원칙

- Gateway/Risk Adapter의 `/health`와 vLLM runtime readiness는 분리해서 판단한다.
- Kanana safeguard 모델은 프로젝트 목적에 맞는 risk signal 모델이며, 장애가 발생해도 곧바로 모델 교체로 결론내지 않는다.
- risk detector의 `bitsandbytes` 설정은 한 GPU에서 여러 runtime을 함께 올리기 위한 기본 양자화 정책이므로 기본 compose에서 제거하지 않는다.
- A/B 검증이 필요하면 별도 debug override에서만 수행하고, 운영 기본값은 명시적으로 유지한다.

## 현재까지 확인한 장애 분류

| 증상 | 원인 분류 | 조치 |
|---|---|---|
| `max_num_batched_tokens is smaller than max_model_len` | vLLM runtime 설정 오류 | pooling/embedding runtime은 `max_num_batched_tokens >= max_model_len`로 수정한다. |
| `fp8_e5m2 kv-cache is not supported with fp8 checkpoints` | 현재 checkpoint/image 조합에서 unsupported한 KV cache dtype | active runtime policy에서 `--kv-cache-dtype`를 제거하고, 32K/O3 등 다른 target을 분리 검증한다. |
| `hidden size ... is not a multiple of the number of attention heads` | transformers 4.52.0–4.52.3 의 `LlamaConfig.validate_architecture` 버그 + `huggingface_hub >= 1.13.0`의 `init_with_validate` 강화 조합 | `make risk-vllm-config-check`로 image 내부 transformers 버전을 확인한다. 4.52.0–4.52.3 이면 4.52.4 이상으로 재빌드한다. `huggingface_hub`를 0.x로 다운그레이드하지 않는다. 모델 교체로 단정하지 않는다. |
| `No available memory for the cache blocks` | KV cache VRAM allocation 부족 | `gpu_memory_utilization`, context length, batching, runtime isolation을 검토한다. |
| `Engine core initialization failed. Failed core proc(s): {}` | vLLM engine subprocess 비정상 종료 (빈 dict는 자식 프로세스가 오류를 보고하기 전에 죽었음을 뜻함). GPU OOM이 가장 흔한 원인 | risk 모델의 `--enforce-eager` 설정 여부, 총 `gpu_memory_utilization`이 `configs/gpu_budgets.yaml`의 `avoid_above` 미만인지, compose의 `depends_on: service_healthy` 순차 기동 체인을 확인한다. |
| Gateway/Risk Adapter healthy인데 `ready-full` 실패 | downstream vLLM readiness 실패 | `make compose-diagnostics`로 vLLM 서비스별 로그를 확인한다. |
| `ready-full`이 timeout까지 계속 `로딩 중` | 최초 모델 다운로드/캐시 생성이 readiness timeout보다 오래 걸리거나 특정 vLLM 컨테이너가 재시작 중 | `READY_FULL_TIMEOUT_SECONDS=2700 make ready-full`로 한 번 더 기다리되, 같은 dependency가 멈춰 있으면 해당 서비스 로그를 확인한다. |
| Grafana Data Quality에서 Gateway/Risk Adapter만 `Scrape Fail`이고 Prometheus target error가 `permission denied` | Prometheus 컨테이너가 `/run/secrets/admin_api_key`를 읽지 못함. distroless Prometheus는 non-root UID로 실행된다. | `make sync-runtime-secrets`가 만든 `.runtime/prometheus/admin_api_key`가 일반 파일이고 `0644`인지 확인한다. 필요 시 `chmod 644 .runtime/prometheus/admin_api_key` 후 Prometheus를 재생성한다. |
| Prometheus target error가 `data does not end with # EOF` | `/metrics` 응답 본문은 Prometheus text format인데 Content-Type이 OpenMetrics로 나가는 불일치 | app image가 `prometheus_client.CONTENT_TYPE_LATEST`를 사용하도록 빌드됐는지 확인한다. 수정 후 platform image를 재빌드/배포한다. |
| `runtime secret 동기화 실패 ... Is a directory: '.runtime/prometheus/admin_api_key'` | 과거 bind mount 또는 수동 작업으로 bearer-token 파일 경로가 디렉터리로 생성됨 | 빈 디렉터리는 `make sync-runtime-secrets`가 파일로 복구한다. 비어 있지 않으면 내용을 확인한 뒤 디렉터리를 제거하고 다시 실행한다. |

## 권장 진단 순서

```bash
make compose-up
make ready-full
# 실패 시 자동 diagnostics가 출력된다. 수동 재확인은 다음 명령을 사용한다.
make compose-diagnostics
READY_MODE=full make status
```

`make ready-full`의 기본 readiness 대기 시간은 `READY_FULL_TIMEOUT_SECONDS=1800`이다. 이 값은 Docker image build timeout이 아니라 Gateway `/ready`가 vLLM dependency 준비를 기다리는 시간이다. 최초 실행에서 Hugging Face 모델 다운로드, gated 모델 인증, 캐시 생성, quantization loader 초기화가 겹치면 30분 이상 걸릴 수 있다.

서비스별 로그만 보고 싶으면 다음 명령을 사용한다.

```bash
docker compose -f ops/compose/full-stack.private-network.yaml --env-file .env logs --tail=160 embedding-vllm
docker compose -f ops/compose/full-stack.private-network.yaml --env-file .env logs --tail=160 risk-prompt-vllm
```

Prometheus scrape 상태는 Grafana 화면보다 Prometheus target API의 `lastError`가 더 직접적이다.

```bash
docker compose -f ops/compose/full-stack.private-network.yaml --env-file .env exec grafana \
  wget -qO- http://prometheus:9090/api/v1/targets
```

Gateway/Risk Adapter가 `healthy`인데 scrape만 실패하면 다음 순서로 분리한다.

```bash
ls -l .runtime/prometheus/admin_api_key
python3 - <<'PY'
from pathlib import Path
import hashlib

env_key = next(
    line.split("=", 1)[1].strip()
    for line in Path(".env").read_text().splitlines()
    if line.startswith("ADMIN_API_KEY=")
)
file_key = Path(".runtime/prometheus/admin_api_key").read_text().strip()
print("match:", env_key == file_key)
print("env/admin hash :", hashlib.sha256(env_key.encode()).hexdigest()[:16])
print("file/admin hash:", hashlib.sha256(file_key.encode()).hexdigest()[:16])
PY
```

판단 기준:

| `lastError` | 의미 | 조치 |
|---|---|---|
| `permission denied` | Prometheus가 bearer token 파일을 읽지 못함 | 파일이 일반 파일인지, 권한이 `-rw-r--r--`인지 확인한다. |
| `401 Unauthorized` | token 값 불일치 | `make sync-runtime-secrets` 후 Prometheus를 재생성한다. |
| `data does not end with # EOF` | OpenMetrics Content-Type과 text body 불일치 | platform image에 metrics Content-Type 수정이 포함됐는지 확인한다. |
| `connection refused` 또는 timeout | compose network/service 접근 문제 | `docker compose ... ps`, service logs, healthcheck를 확인한다. |

## risk-prompt 검증 정책

`risk-prompt-vllm`은 `kakaocorp/kanana-safeguard-prompt-2.1b`와 `bitsandbytes` 기본값을 유지한다.

Docker/GPU를 올리기 전에 HF config loader만 분리하려면 다음을 실행한다.

```bash
python3 scripts/models/check_hf_model_config.py --model kakaocorp/kanana-safeguard-prompt-2.1b
```

호스트 venv에서 통과한 뒤에는 반드시 실제 risk runtime image 내부에서도 같은 검사를 수행한다. 일반 경로에서는 `make first-run`/`make bootstrap`과 `make preflight-compose`가 이 검사를 자동 수행한다. 수동으로 image만 재검증할 때는 다음을 실행한다.

```bash
make rebuild-vllm-unified
make risk-vllm-config-check
```

`transformers>=4.52.4`에서 이 명령이 `status=ok`를 반환하면서 `hidden_size_divisible_by_attention_heads=False`와 `requires_runtime_head_dim_support=True`를 표시하면, HF config 자체는 로드됐고 explicit `head_dim`을 vLLM runtime이 존중해야 하는 모델이라는 뜻이다.

반면 `classification=CONFIG_VALIDATION_HIDDEN_HEAD_MISMATCH`로 실패하는 경우, image 내부에 transformers 4.52.0–4.52.3 이 설치되어 있을 가능성이 높다. 이 버전 범위는 explicit `head_dim`이 있어도 divisibility를 강제하는 버그가 있었으며 4.52.4에서 수정됐다.

근본 원인: `huggingface_hub >= 1.13.0`은 `init_with_validate` 강화로 `AutoConfig.from_pretrained` 시점에 `validate_architecture`를 호출한다. 기반 vLLM image(2026년 5월 릴리스)는 이미 `huggingface_hub >= 1.x`를 포함하므로, transformers 4.52.0–4.52.3과 조합하면 반드시 이 오류가 발생한다. **`huggingface_hub`를 0.x로 다운그레이드하는 것은 major version regression이며 잘못된 해결책이다.** `make rebuild-vllm-unified`로 transformers >= 4.52.4로 재빌드하는 것이 올바른 조치다.

이 상태에서 vLLM 컨테이너만 `hidden size ... attention heads`로 실패하면 `bitsandbytes`보다 vLLM image 내부의 Transformers 버전을 우선 의심한다.

장애 원인 분리를 위해 `bitsandbytes` ON/OFF를 비교할 수는 있지만, 그 실험은 별도 override 또는 일회성 컨테이너에서만 수행한다. 기본 compose의 `--quantization bitsandbytes`와 `--load-format bitsandbytes`를 제거하지 않는다.

판단 기준:

| 결과 | 해석 |
|---|---|
| bnb ON/OFF 모두 같은 config validation 실패 | quantization 문제가 아니라 vLLM/Transformers loader 호환성 문제 가능성이 높다. |
| bnb ON만 실패, OFF 성공 | bitsandbytes load path 호환성 문제다. 운영 대안은 다른 quantization/image 조합 검증이다. |
| 둘 다 성공 | 기존 장애는 image/cache/env/일시 상태였을 수 있으므로 compose 재현성을 다시 확인한다. |

## risk 모델 GPU OOM 대응 정책

enabled risk 모델(`risk-prompt-vllm`)에는 다음 설정이 적용된다. `risk-siren`은 retired 상태이며 기본 compose, readiness, aggregate execution에서 제외된다.

| 설정 | 값 | 이유 |
|---|---|---|
| `--enforce-eager` | 활성화 | CUDA graph pre-capture를 비활성화한다. 모델당 300~500MiB 절약. `max_num_seqs=1`, `max_output_tokens=1` 단일 토큰 분류기에서 CUDA graph 이득이 없다. |
| `gpu_memory_utilization` | risk-prompt 0.065 | Dense retrieval-ko 포함 4-runtime enabled vLLM 총합은 0.925로 `avoid_above` 0.93 바로 아래다. 추가 runtime, context 증가, concurrency 증가는 별도 검증 없이 허용하지 않는다. |

`Engine core initialization failed. Failed core proc(s): {}` 오류가 보이면 위 설정이 compose에 반영됐는지 `make vllm-commands`로 확인한다.

## preflight에서 잡는 항목

`make preflight-compose`는 `scripts/compose/validate_vllm_compose.py`를 실행해서 다음을 사전에 확인한다.

- compose command와 `configs/model_serving.yaml` 값 정합성
- Embedding pooling runtime의 `max_num_batched_tokens >= max_model_len`
- risk detector의 `bitsandbytes` 양자화 기본값 유지
- `RISK_VLLM_IMAGE` 내부에서 enabled Kanana Prompt 2.1B HF config 파싱 검증
- model catalog, model card, serving config의 핵심 runtime policy 정합성
- conservative single-GPU profile의 총 `gpu_memory_utilization` 상한

## 운영 확정 전 남은 검증

- target GPU에서 `make compose-up` 후 `make ready-full`
- `python scripts/validation/runtime_validation.py --skip-soak`
- `python scripts/validation/runtime_validation.py --soak-seconds 1800 --concurrency 1`
- Python 3.12/3.13/3.14별 app/control-plane test와 full-stack image compatibility 확인

## risk vLLM image 분리 정책

`RISK_VLLM_IMAGE`는 main `VLLM_IMAGE`와 별도 운영 단위다. Prompt 2.1B는 explicit `head_dim`이 필요한 Llama 변형이므로, preflight는 enabled risk 모델을 image 내부에서 검사한다.

기본값은 `ai-model-serving-risk-vllm-kanana:<version>`이며 `make first-run`/`make bootstrap`이 생성하고 검증한다. host venv에서 `check_hf_model_config.py`가 통과했더라도 compose 전 image 내부 config check가 통과해야 한다.


## Patch lifecycle 참조

Risk vLLM image patch metadata와 제거 조건은 [Risk vLLM patch 생명주기](./risk_vllm_patch_lifecycle.md)에서 추적한다.
