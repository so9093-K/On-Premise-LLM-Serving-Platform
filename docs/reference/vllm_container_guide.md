# vLLM Container 실행 가이드

프로젝트의 **Unified vLLM 이미지**로 단일 vLLM Container를 직접 실행하고, 작은 모델을 로드한 뒤 OpenAI-compatible API 요청까지 확인하는 과정을 정리한다.

최종 확인 지점은 다음과 같다.

```text
GPU 확인
   ↓
Unified vLLM Image 준비
   ↓
Container 실행
   ↓
Model Load
   ↓
API 준비 상태 확인
   ↓
직접 Chat 요청
   ↓
응답 확인
   ↓
Container 종료
   ↓
정리 확인
```

기본 실행 경로는 vLLM Container 기동부터 직접 API 응답 확인까지다. Gateway 연동, Main Model 전환, Risk Adapter, 전체 Platform 배포와 운영 절차는 각 관련 문서에서 이어진다.

---

## 1. 실행 구성

예제 모델로 [`Qwen/Qwen3-0.6B`](https://huggingface.co/Qwen/Qwen3-0.6B)를 사용한다. 작은 생성 모델을 이용해 **Container 기동부터 실제 HTTP 응답까지의 기본 경로**를 확인한다.

vLLM 실행 구성은 모델과 실행 환경의 조합에 따라 달라진다. GPU 종류와 메모리, Driver/CUDA 환경, vLLM 버전과 Container Image, 모델의 지원 조건과 실행 옵션이 실제 기동 방식과 API 동작에 함께 영향을 준다.

새로운 모델이나 다른 실행 환경에서는 해당 모델과 사용 중인 vLLM 버전의 지원 조건을 확인한 뒤 실행 값을 구성한다. 이 예제는 vLLM Container의 기본 실행 흐름을 직접 확인하기 위한 하나의 동작 구성이다.

```text
Model
  ↓
모델과 Runtime 조건 확인
  ↓
Container 실행 구성
  ↓
OpenAI-compatible API 직접 요청
  ↓
실제 동작 확인
```

---

## 2. 사전 준비

실행 환경에는 Docker, NVIDIA Container Runtime과 NVIDIA GPU가 필요하다.

현재 환경의 GPU를 확인한다.

```bash
nvidia-smi
```

Docker에서 GPU를 사용할 수 있는 환경인지 확인한다.

```bash
docker version
```

별도 vLLM Runtime이 GPU 자원을 사용한다. 실행 전 `nvidia-smi`로 현재 GPU 메모리 사용량을 확인하고 모델 로드에 필요한 여유 자원을 확보한다.

### 2.1 Unified vLLM 이미지 준비

Linux x86_64 NVIDIA 호스트의 프로젝트 루트에서 Unified vLLM 이미지를 빌드한다.

```bash
make build-vllm-unified-image
```

이 CUDA image build는 macOS/arm64에서 지원하지 않는다. M5 Metal runtime은 별도
환경과 모델 qualification을 사용하며 이 image를 cross-build해 대체하지 않는다.

현재 프로젝트 버전을 기준으로 사용할 이미지와 모델 cache 경로를 설정한다.

```bash
export VLLM_IMAGE="${VLLM_IMAGE:-ai-model-serving-vllm-unified:$(cat VERSION)}"
export HF_CACHE_DIR="${HF_CACHE_DIR:-$PWD/model_cache/huggingface}"
mkdir -p "$HF_CACHE_DIR"
```

이미지를 확인한다.

```bash
docker image inspect "$VLLM_IMAGE" \
  --format '{{.RepoTags}} {{.RepoDigests}}'
```

Unified 이미지의 기반 vLLM 이미지와 dependency 조합은 `configs/vllm_unified_build.yaml`에서 관리한다. 이미지 빌드 구조는 [7. 로컬 개발 및 빌드](../07_local_dev_build.md)를 참고한다.

---

## 3. 작은 모델 실행

Standalone Container 이름은 `vllm-quickstart`, Host port는 `9410`을 사용한다.

API는 테스트 Host 내부에서만 접근할 수 있도록 `127.0.0.1`에 bind한다.

```bash
docker run -d --rm \
  --name vllm-quickstart \
  --gpus all \
  --ipc=host \
  -p 127.0.0.1:9410:8000 \
  -v "$HF_CACHE_DIR:/root/.cache/huggingface" \
  "$VLLM_IMAGE" \
  --model Qwen/Qwen3-0.6B \
  --served-model-name quickstart-model \
  --host 0.0.0.0 \
  --port 8000 \
  --max-model-len 2048 \
  --max-num-seqs 1 \
  --gpu-memory-utilization 0.20
```

주요 실행 값은 다음과 같다.

| 항목 | 값 | 역할 |
|---|---|---|
| Container | `vllm-quickstart` | 예제 Container 식별자 |
| GPU | `--gpus all` | 현재 Host의 GPU를 Container에 연결 |
| Host port | `127.0.0.1:9410` | Host 내부 테스트 API 주소 |
| Container port | `8000` | vLLM API Server port |
| Model | `Qwen/Qwen3-0.6B` | 기본 동작 확인용 작은 모델 |
| Served model | `quickstart-model` | API 요청에서 사용할 모델 이름 |
| Context | `2048` | 예제 최대 모델 길이 |
| Sequence | `1` | 예제 동시 처리 수 |
| GPU memory | `0.20` | 예제 Runtime의 vLLM GPU 메모리 설정 |

`--gpu-memory-utilization 0.20`은 이 작은 모델을 이용한 기본 예제 값이다. GPU 종류와 메모리 상태에 따라 기동 가능 범위는 달라질 수 있다.

Container 이름은 `vllm-quickstart`를 사용한다. 동일한 이름이 이미 등록되어 있으면 기존 Container의 용도를 확인한 뒤 다른 이름으로 실행한다.

---

## 4. Model Load 확인

최초 실행에서는 모델 다운로드와 초기화에 시간이 걸릴 수 있다.

Container 상태를 확인한다.

```bash
docker ps --filter name=vllm-quickstart
```

시작 로그를 확인한다.

```bash
docker logs -f vllm-quickstart
```

모델 다운로드, GPU 초기화와 vLLM Engine 구성이 끝나고 API Server가 요청을 받을 수 있는 상태가 될 때까지 기다린다.

로그 확인을 종료할 때는 `Ctrl+C`를 사용한다. Container는 계속 실행된다.

GPU에서도 vLLM 프로세스를 확인할 수 있다.

```bash
nvidia-smi
```

---

## 5. API 준비 상태 확인

### 5.1 Health

다른 Terminal에서 vLLM health endpoint를 호출한다.

```bash
curl -fsS http://127.0.0.1:9410/health >/dev/null \
  && echo "vLLM health: OK"
```

다음과 같이 출력되면 API Server가 요청을 받을 수 있는 상태다.

```text
vLLM health: OK
```

### 5.2 Model 목록

OpenAI-compatible model 목록을 확인한다.

```bash
curl -fsS http://127.0.0.1:9410/v1/models \
  | python3 -m json.tool
```

응답의 `data`에 `quickstart-model`이 포함되어 있는지 확인한다.

```text
Container 실행
    ↓
Model Load
    ↓
/health 성공
    ↓
/v1/models에 quickstart-model 노출
```

---

## 6. 직접 Chat 요청

실제 생성 요청을 `/v1/chat/completions`로 보낸다.

Qwen3 계열의 reasoning 동작을 단순화하기 위해 요청의 `chat_template_kwargs`에 `enable_thinking: false`를 적용한다.

```bash
curl -fsS http://127.0.0.1:9410/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "quickstart-model",
    "messages": [
      {
        "role": "user",
        "content": "Reply with the single word OK."
      }
    ],
    "max_tokens": 16,
    "temperature": 0,
    "chat_template_kwargs": {
      "enable_thinking": false
    }
  }' \
  | python3 -m json.tool
```

확인 기준은 다음 두 가지다.

1. 요청이 HTTP 성공 응답으로 처리된다.
2. 응답의 `choices[0].message.content`에 생성 결과가 포함된다.

이 요청에서는 생성 문구 자체보다 **모델이 로드된 vLLM Server가 OpenAI-compatible Chat 요청을 실제 생성 결과까지 처리하는지**를 확인한다.

최종 성공 흐름은 다음과 같다.

```text
Docker Container
      ↓
NVIDIA GPU 연결
      ↓
vLLM Engine
      ↓
Model Load
      ↓
OpenAI-compatible API
      ↓
Chat Completion
      ↓
Generated Response
```

여기까지 확인되면 기본 실행 경로가 완료된다.

---

## 7. 실행 상태 확인

### Container

```bash
docker ps --filter name=vllm-quickstart
```

### Runtime 로그

```bash
docker logs --tail 100 vllm-quickstart
```

### GPU

```bash
nvidia-smi
```

이 세 가지 정보로 Container 상태, vLLM Engine 상태와 GPU 사용 여부를 함께 확인할 수 있다.

---

## 8. 주요 실행 오류

처음 vLLM을 실행할 때는 오류가 발생한 단계를 기준으로 확인하는 편이 빠르다.

| 증상 | 우선 확인 영역 |
|---|---|
| Container가 시작되지 않음 | Docker command, Container 이름, Image |
| GPU를 찾지 못함 | NVIDIA Driver, NVIDIA Container Runtime, Docker GPU 연결 |
| 모델 다운로드 실패 | Network, Hugging Face 접근, cache 권한 |
| Model Load 실패 | 모델과 현재 vLLM/Transformers 조합, checkpoint 지원 |
| CUDA / GPU Memory 오류 | GPU 사용량, 모델 크기, `gpu-memory-utilization` |
| `/health` 연결 실패 | Container 상태, startup log, port binding |
| `/v1/models`는 성공하지만 Chat 실패 | 모델의 Chat 지원 방식, chat template, 요청 옵션 |

### Container 상태

```bash
docker ps -a --filter name=vllm-quickstart
```

### 전체 시작 로그

```bash
docker logs vllm-quickstart
```

기본 실행 명령은 `--rm`을 사용하므로 Container 종료와 함께 해당 Container 로그도 정리된다. 기동 상태를 자세히 확인할 때는 `--rm`을 제외해 Container와 로그를 유지할 수 있다.

### GPU 상태

```bash
nvidia-smi
```

GPU 메모리 사용량은 모델 로딩과 KV Cache 확보 가능 범위에 직접 영향을 준다.

### Port 상태

```bash
ss -ltnp | grep ':9410'
```

Host port는 사용 가능한 값을 선택한다. `9410`을 다른 값으로 변경한 경우 이후 `curl` 주소에도 같은 port를 적용한다.

---

## 9. 종료 및 정리

확인을 마치면 Container를 종료한다.

```bash
docker stop vllm-quickstart
```

실행 명령의 `--rm` 옵션에 따라 종료된 Container가 자동으로 삭제된다.

Container 정리 상태를 확인한다.

```bash
docker ps -a --filter name=vllm-quickstart
```

출력이 없으면 Container 정리가 완료된 상태다. Container 종료와 함께 `127.0.0.1:9410`의 port binding이 해제되고 vLLM이 사용하던 GPU 메모리도 반환된다.

GPU 상태를 다시 확인한다.

```bash
nvidia-smi
```

모델 Cache와 Unified vLLM 이미지는 이후 실행에서 재사용할 수 있도록 Host에 유지된다.

```bash
du -sh "$HF_CACHE_DIR"
docker image inspect "$VLLM_IMAGE" --format '{{.RepoTags}} {{.Size}}'
```

정리 범위는 다음과 같다.

| 항목 | 종료 후 상태 | 용도 |
|---|---|---|
| Container | 삭제 | 실행 단위 정리 |
| Host port `9410` | 해제 | 다음 실행에서 재사용 가능 |
| GPU 메모리 | 반환 | 다른 Runtime에서 사용 가능 |
| 모델 Cache | 유지 | 동일 모델 재실행 시 다운로드 재사용 |
| Unified vLLM Image | 유지 | 이후 Container 실행과 프로젝트 Runtime에서 재사용 |

모델 Cache까지 정리할 경우 `$HF_CACHE_DIR`의 경로와 내용을 확인한 뒤 필요한 모델 데이터만 정리한다.

---

## 10. 다음 단계

범위는 **Standalone vLLM Container 실행과 직접 API 요청**까지다.

vLLM 직접 요청까지 성공한 뒤 Platform과의 연동을 확인할 때는 다음 문서를 사용한다.

| 문서 | 연결 내용 |
|---|---|
| [4. 실행 환경](../04_runtime_modes.md) | Platform Runtime 구성 |
| [5. 설정](../05_configuration.md) | 모델·서비스·GPU 설정 |
| [6. 모델 운영](../06_model_operations.md) | Main Model 실행과 전환 |
| [7. 로컬 개발 및 빌드](../07_local_dev_build.md) | Unified vLLM 이미지 빌드 |
| [8. 테스트 및 검증](../08_testing_validation.md) | Platform 검증 단계 |
| [API 인터페이스](api_reference.md) | Gateway OpenAI-compatible API |

Gateway를 포함한 End-to-End 검증, Runtime 운영과 장애 대응은 각 Platform 문서로 이어진다.
