# 7. 로컬 개발과 빌드

AI Model Serving Platform의 로컬 개발은 **application 개발**, **full-stack 통합 확인**, **Docker image build**, **release package 생성**으로 나뉜다.

일반적인 개발 흐름은 코드 변경 후 정적 검증과 테스트를 수행하고, 변경 범위에 맞는 실행 환경에서 동작을 확인하는 순서로 진행한다.

```text
코드 변경
   ↓
make validate
   ↓
make test
   ↓
실행 환경 선택
   ├─ app-only  → application layer 확인
   └─ full-stack → 실제 vLLM / GPU 통합 확인
   ↓
필요한 Artifact Build
   ├─ Platform Image
   ├─ Unified vLLM Image
   └─ Release ZIP
```

Runtime 구조와 실행 모드는 [4. 실행 환경과 모드](./04_runtime_modes.md), 설정 파일과 환경변수는 [5. 설정 체계와 Source of Truth](./05_configuration.md), 상세 테스트 구성은 [8. 테스트와 검증](./08_testing_validation.md)에서 설명한다.

---

## 7.1 개발 환경 확인

개발 작업에 필요한 환경은 작업 범위에 따라 달라진다.

| 작업 | 주요 요구사항 |
|---|---|
| Application 개발·검증·테스트 | Python `>=3.12,<3.15` |
| Platform Image Build | Docker CLI / Docker daemon. 로컬 기본 target은 daemon architecture |
| Full-stack 실행 | Bash 4 이상, native Linux amd64 Docker daemon, NVIDIA GPU/driver/Container Toolkit |
| Unified vLLM Image Build | `vllm_unified_build.yaml` target과 같은 native Docker daemon. CUDA/NVIDIA image 전용 |
| Model 다운로드 | Hugging Face token 및 모델별 사용 조건 |

프로젝트가 지원하는 Python 범위는 `pyproject.toml`의 `requires-python`을 기준으로 하며, 현재 CPython 3.12, 3.13, 3.14를 지원한다. 오래된 Python에서도 먼저 오류를 안내할 수 있도록 bootstrap guard에도 같은 범위가 있고, `make validate`가 두 값의 일치를 확인한다.

Linux 운영 image의 기준은 `.python-version`에 고정된 Python patch와 Dockerfile의 base image digest다. GitHub app/contract CI는 그 patch에서 major.minor를 계산하고 Ubuntu와 macOS ARM64 runner가 제공하는 patch를 사용한다. 3.13과 3.14도 application/control-plane 범위에서는 지원하지만, vLLM·PyTorch·CUDA wheel/ABI와 GPU driver 조합은 minor version마다 다르므로 full-stack 운영 지원은 해당 minor의 `make runtime-validate` 결과로 확인한다.

로컬 Make 명령은 프로젝트의 `.venv`가 존재하면 해당 Python을 우선 사용한다. 호출자가 `PYTHON_BIN`을 지정한 경우에는 지정된 interpreter를 사용한다.

### macOS / Ubuntu 개발 환경 준비

애플리케이션 환경 준비와 정적 검증·테스트에는 Bash 4를 강제하지 않는다.

```bash
brew install python@3.12
make setup-dev
make validate
make test
```

Ubuntu에서는 지원 Python과 해당 버전의 `venv` 패키지를 준비한 뒤 같은 Make 명령을 사용한다. `.python-version`은 Linux 운영 기준 patch이며, GitHub의 cross-platform app/contract CI는 여기서 계산한 minor를 사용한다. Homebrew의 versioned Python formula도 설치 시점에 제공되는 patch를 사용하므로 운영 image의 exact patch/digest 검증과 구분한다.

별도로 설치한 Python을 쓰려면 `make setup-dev PYTHON_BIN=/path/to/python`으로 지정한다.

환경 파일 helper와 일부 Compose·배포 스크립트는 associative array나 `mapfile`을 사용하므로 Bash 4 이상이 필요하다. macOS에서 해당 운영 명령까지 실행하려면 `brew install bash` 후 Homebrew Bash를 PATH에 추가한다. `make doctor-dev`는 실제 Python 경로와 Bash 버전, 기준 Python 버전을 함께 확인하는 운영 도구 진단이다. 기본 로그인 shell(zsh)을 바꿀 필요는 없다.

`setup-dev`는 `requirements.lock`을 `--no-deps`로 설치하고, `pyproject.toml`의 build backend를 준비한 뒤 editable package 설치와 `pip check`를 수행한다. 누락된 하위 의존성을 설치 시점의 최신 버전으로 조용히 채우지 않는다. 기존 `.venv`는 실행 중인 선택 interpreter와 minor가 같을 때 재사용한다. 새 `.venv`를 만드는 경우에는 Python `venv`가 실제로 사용하는 base interpreter의 minor를 먼저 확인하고 그 executable로 생성한다. 선택 interpreter와 base가 다르면 잘못된 minor의 환경을 만들지 않고 명시적으로 중단하며, 기존 환경이나 부분 생성 결과는 자동 삭제하지 않는다.

이 명령은 `.env`와 runtime state를 생성·변경하지 않는다. GPU bootstrap인 `make first-run`은 별도 운영 절차로 유지한다. macOS app/contract 검증 통과가 `macos-metal-static` 모델 runtime의 qualification을 의미하지는 않는다([ADR-0020](adr/0020-runtime-control-and-deployment-targets.md)).

빌드 재현성의 범위도 실행 환경별로 구분한다.

| 경로 | 고정되는 입력 | 결과의 의미 |
|---|---|---|
| 로컬 `make build-image` | Dockerfile base digest, runtime lock, 현재 working tree | 변경 중인 코드를 확인하는 로컬 image ID |
| GitHub Actions | application lock과 Python minor | macOS/Ubuntu app·contract 검증. image artifact 없음 |
| GitLab Platform build | clean commit, Linux amd64 target, base digest, runtime lock | registry에 push된 운영 후보 digest |
| GitLab Unified vLLM build | clean commit, Linux amd64 target, vLLM base digest와 compatibility pin | NVIDIA runtime 후보 digest |

같은 Dockerfile과 build script를 공유하는 것은 입력 해석을 맞추기 위한 것이다. 로컬의
수정된 working tree나 arm64 image ID가 GitLab의 clean Linux amd64 registry digest와
byte-identical하다는 뜻은 아니다. 실제 배포 identity는 계속 registry digest가 소유한다.

### Linux dependency lock 갱신

`pyproject.toml`이 direct dependency의 Source of Truth이고,
`requirements.runtime.lock`과 `requirements.lock`은 각각 운영 image와
application/contract 환경의 해석 결과다. Lock 갱신은 host OS의 Python이 아니라
Docker의 고정 Linux amd64 resolver에서 다음 명령으로 수행한다.

```bash
make lock-linux
```

이 명령은 어느 host에서 호출하더라도 `--platform linux/amd64`를 명시하고,
Dockerfile과 GitLab CI가 공유하는 digest 고정
`python:<.python-version>-slim@sha256:...` image 안에서
`pip==26.0.1`, `pip-tools==7.5.3`을 사용한다.
기존 lock을 resolver constraint로 재사용하므로, lock 재생성 자체가 사전 검토 없이
전이 의존성 전체를 업그레이드하지 않는다.
두 lock을 임시 경로에 먼저 만들고 각각 새 venv에 설치해 `pip check`와 contract
validation이 통과한 뒤에만 저장소 파일을 교체한다. 실패하면 기존 lock을 복원한다.
macOS host Python이나 임의 Python에서 `pip freeze`한 결과로 운영 lock을 갱신하지
않는다. 운영 반영 전 최종 설치 확인은 계속 Ubuntu amd64 환경에서 수행한다.

Full-stack 환경에서는 NVIDIA GPU와 NVIDIA Container Toolkit을 통해 vLLM container가 GPU에 접근한다. Hugging Face에서 모델을 가져오는 runtime은 `.env`의 `HF_TOKEN` 또는 `HUGGING_FACE_HUB_TOKEN`을 사용한다.

환경 파일의 생성과 관리 방식은 [5.10 환경 파일](./05_configuration.md#510-환경-파일)을 참고한다.

---

## 7.2 기본 개발 흐름

일반적인 source 변경은 다음 순서로 확인한다.

```bash
make validate
make test
```

검증이 완료되면 변경 범위에 맞는 실행 환경을 선택한다.

| 변경 범위 | 권장 확인 환경 |
|---|---|
| Gateway routing / validation | app-only |
| Authentication / error handling | app-only |
| Risk Adapter application logic | app-only |
| Main Model inference | full-stack |
| Embedding / Retrieval runtime | full-stack |
| Prompt Risk vLLM | full-stack |
| GPU / Runtime lifecycle | full-stack |
| Dockerfile / application dependency | Platform Image Build |
| vLLM base / compatibility pin / runtime patch | Unified vLLM Image Build |

### Application 변경

```text
Source 변경
   ↓
make validate
   ↓
make test
   ↓
make start
   ↓
make ready-local
```

### Runtime 통합 변경

```text
Source / Config 변경
   ↓
make validate
   ↓
make test
   ↓
make compose-up
   ↓
make ready-full
```

`make validate`와 `make test`가 검사하는 세부 항목은 [8. 테스트와 검증](./08_testing_validation.md)에서 다룬다.

---

## 7.3 app-only 실행

app-only는 Gateway와 Risk Adapter를 로컬 Python process로 실행하는 개발 방식이다.

### 환경 준비

```bash
make init-env-local
```

`make init-env-local`은 `.env.local.example`을 기준으로 app-only용 `.env`를 준비한다.

### 서비스 실행

```bash
make start
```

`make start`는 다음 application process를 실행한다.

```text
Developer Host
│
├─ Gateway       localhost:9400
└─ Risk Adapter  localhost:9405
```

각 process를 시작한 뒤 `/health` 응답을 확인하고 실행 결과를 `run/`과 `logs/`에 기록한다.

### 상태 확인

```bash
make ready-local
```

`make ready-local`은 Gateway와 Risk Adapter의 localhost `/health`를 확인한다.

app-only는 다음과 같은 application layer 작업에 적합하다.

- API routing
- request validation
- authentication
- error mapping
- OpenAPI / schema 변경
- Gateway와 Risk Adapter 로직

### 종료

```bash
make stop
```

app-only와 full-stack의 구조적 차이는 [4.1 실행 모드](./04_runtime_modes.md#41-실행-모드)를 참고한다.

---

## 7.4 full-stack 실행

full-stack은 Docker Compose를 사용해 application, model runtime, control plane, observability를 함께 실행한다.

### 환경 준비

새로운 full-stack 개발 환경은 bootstrap으로 Python environment와 Docker image를 함께 준비한다.

```bash
HF_TOKEN=hf_xxx make first-run
```

이미 build artifact가 준비된 환경에서 Compose용 `.env`만 생성할 때는 다음 명령을 사용한다.

```bash
make init-env-compose
```

Hugging Face에서 모델을 가져오는 runtime은 `.env`에 설정된 token을 사용한다.

### Stack 실행

```bash
make compose-up
```

`make compose-up`은 다음 준비 작업을 수행한 뒤 effective Compose stack을 기동한다.

1. `.env` contract 검증
2. runtime secret 준비
3. Exposure Profile 적용
4. persisted Main Model profile을 반영한 boot projection 생성
5. 같은 boot projection으로 effective Compose config와 preflight 검증
6. Hugging Face cache 준비
7. 서비스 기동

Preflight와 기동은 같은 `base → exposure override → boot override` 순서를 사용한다. `compose-up`에서 생성한 boot 파일을 `--boot-override`로 전달하므로 preflight 중 persisted state를 다시 읽어 다른 프로필을 고르지 않는다. Preflight를 단독 실행하면 기존 boot resolver로 임시 override를 만들고 종료 시 삭제한다.

정상 preflight 뒤에는 같은 `docker compose config` 검사를 반복하지 않는다. 기존 정책에 따라 명시적으로 preflight를 생략한 경우에만 별도 config 검사를 실행한다.

메인의 effective image·command는 이 boot projection과 비교하며, GPU 예산 합계에도 실제 command의 host override를 반영한다. 보조 모델 command와 Sidecar admission은 계속 `model_serving.yaml`의 같은 고정 예산을 기준으로 검사한다. 보조 모델에 별도 host override 계약은 두지 않는다.

실제 host port 공개 범위는 `EXPOSURE_MODE`에 따라 결정된다. 자세한 내용은 [4.3 네트워크와 서비스 노출](./04_runtime_modes.md#43-네트워크와-서비스-노출)을 참고한다.

### 준비 상태 확인

```bash
make ready-full
```

`make ready-full`은 Gateway와 vLLM dependency의 준비 상태를 확인하고, 실제 inference path까지 순차적으로 검증한다.

Full-stack은 다음 작업에 사용한다.

- Main Model inference
- Embedding / Retrieval
- Prompt Risk runtime
- Main Model switching
- GPU resource admission
- Docker runtime lifecycle
- Observability 연동

### Stack 종료

```bash
make compose-down
```

---

## 7.5 Platform Image Build

Platform Image는 Gateway, Risk Adapter, Admin / Control Sidecar 등 application과 control-plane 코드를 실행하는 Docker image다.

기본 image tag는 프로젝트 `VERSION`을 사용한다.

```text
ai-model-serving-platform:<VERSION>
```

### 전체 Build Gate

```bash
make build
```

`make build`은 다음 순서로 진행된다.

```text
make validate
   ↓
make test
   ↓
Platform Docker Image Build
   ↓
Image 내부 Application Import 검증
```

일반적인 release 후보 또는 application image 변경 확인에는 `make build`을 사용한다.

### Image만 Build

```bash
make build-image
```

`make build-image`는 `Dockerfile`을 사용해 Platform Image를 생성하고, 생성된 image 안에서 Gateway와 Risk Adapter application factory를 실제로 import·초기화한다.

| 명령 | 범위 |
|---|---|
| `make build` | `validate → test → Platform Image Build` |
| `make build-image` | Platform Image Build + image 내부 application 확인 |

`PLATFORM_IMAGE` 환경변수로 build tag를 지정할 수 있으며, 기본값은 `ai-model-serving-platform:<VERSION>`이다.

이 값은 호출 process의 명시적 build override이며 runtime `.env`를 암묵적으로 읽지
않는다. Runtime image 선택과 build output tag를 분리해 `.env`의 운영 설정이나
credential이 Docker build process에 불필요하게 로드되지 않게 한다.

로컬 기본 build target은 Docker daemon의 architecture다. 예를 들어 Apple Silicon의
Docker Desktop에서는 일반적으로 Linux arm64 image가 생성된다. 운영 target과 같은
architecture의 application image가 필요한 경우 다음처럼 명시할 수 있다.

```bash
PLATFORM_BUILD_PLATFORM=linux/amd64 make build-image
```

Build 로그와 image label에는 Git revision, working tree의 clean/dirty 상태와 target
platform이 남는다. dirty 상태는 개발 중 image로 허용하지만 clean-commit CI artifact로
오인하지 않도록 경고한다. 로컬 tag는 mutable하므로 배포 입력으로 사용하지 않는다.

Platform Image는 로컬과 GitLab CI에서 동일한 `scripts/build/build_platform_image.sh`를 사용한다. Unified vLLM Image도 로컬과 GitLab이 `scripts/build/build_vllm_unified_image.sh`의 같은 Docker build를 사용하며, CI wrapper는 변경 감지·추가 tag·push·digest만 담당한다. GitHub Actions는 image를 빌드하지 않는다. Pipeline 동작은 [9. CI/CD](./09_cicd.md)에서 설명한다.

---

## 7.6 Unified vLLM Image Build

Main Model, Embedding, Korean Embedding, Prompt Risk runtime은 하나의 **Unified vLLM Image**를 공유한다.

```text
Unified vLLM Image
├─ Main Model
├─ Embedding
├─ Korean Embedding
└─ Prompt Risk
```

기본 build 명령은 다음과 같다.

```bash
make build-vllm-unified-image
```

이 명령은 CUDA 기반 운영 image를 만드는 경로다. Host OS 이름을 판정하지 않고
`configs/vllm_unified_build.yaml`의 `target_platform`과 Docker daemon platform이
일치하는지 확인한다. 현재 target은 `linux/amd64`이며 emulation build는 지원 범위에
넣지 않는다. M5 Metal은 CUDA image의 cross-build가 아니라 별도 runtime 환경과 모델
qualification으로 진행한다.

Unified vLLM Image의 주요 build 입력은 다음과 같다.

| 입력 | 역할 |
|---|---|
| `ops/images/vllm-unified/Dockerfile` | Derived runtime image 구성 |
| `configs/vllm_unified_build.yaml` | Target platform, base image와 compatibility pin 관리 |
| `ops/images/vllm-unified/requirements.media.lock` | Multimodal media dependency 고정 |
| `ops/patches/apply_gemma4_multimodal_patches.py` | Gemma4 multimodal compatibility patch |
| `ops/patches/transformers_llama_head_dim_guard.py` | Prompt Risk Llama `head_dim` compatibility patch |

Build script는 `configs/vllm_unified_build.yaml`에서 base image와 compatibility version을 읽어 Docker build argument로 전달한다.

Unified vLLM Image는 다음 변경에서 다시 빌드한다.

- vLLM base image 변경
- Transformers / Hugging Face compatibility pin 변경
- media dependency 변경
- runtime patch 변경
- Unified vLLM Dockerfile 변경

일반 application source 변경은 Platform Image build 흐름에서 확인한다.

Unified vLLM build 입력 변경을 CI가 감지하고 derived image를 만드는 과정은 [9. CI/CD](./09_cicd.md)에서 설명한다.

`make first-run`은 로컬에서 빌드한 Unified image tag를 Docker의 content-addressed
`sha256:...` image ID로 해석하고, `.env`에서 그 build tag와 정확히 일치하는 unified
image 값만 고정한다. 운영자가 별도로 지정한 image ref는 추측해서 덮어쓰지 않는다.
GitLab 배포는 이 로컬 ID 대신 registry의 `name@sha256:...` digest를 사용한다.

### 반복 개발에서 Unified Image Build 생략

앱 코드만 반복 수정하고 이미 검증된 Unified vLLM image를 유지할 때는 다음 명령을 사용할 수 있다.

```bash
SKIP_RISK_VLLM_IMAGE_BUILD=auto make first-run
```

`auto`는 `.env`의 `RISK_VLLM_IMAGE` tag가 로컬에 있을 때만 Unified image build를 생략하고, 없으면 build한다. 명시적으로 image를 관리하는 경우에는 `SKIP_RISK_VLLM_IMAGE_BUILD=1`을 사용할 수 있다.

이 최적화는 `Dockerfile`, patch, media dependency, base digest 또는 compatibility pin이 바뀌지 않았을 때만 사용한다. 이들 입력이 바뀌면 `make build-vllm-unified-image` 또는 `make first-run`으로 새 image를 만들고 full-stack 검증을 수행한다.

---

## 7.7 Release Package

배포용 source artifact는 ZIP package로 생성한다.

```bash
make package
```

기본 출력은 다음과 같다.

```text
dist/ai_model_serving_platform_<VERSION>.zip
```

Release package에는 Git이 추적하는 파일 중 실행에 필요한 source, config, spec, ops
artifact, 테스트와 `env_contract.yaml`에 선언된 안전한 `.env.*.example` 파일이
포함된다. GitHub Actions workflow는 배포 artifact에 포함하지 않는다.

Packaging 과정에서는 다음 항목을 배포 artifact에서 분리한다.

- `.venv/`
- local log / run data
- model cache
- runtime-generated state와 report
- 실제 `.env`
- private key / secret file
- local tool / private workspace directory

ZIP entry의 timestamp는 고정값을 사용해 동일한 source에서 재생 가능한 package 형태를 유지한다.
패키지 입력은 현재 working tree의 Git tracked 파일로 한정해, 로컬의 untracked
메모나 임시 파일이 같은 commit의 ZIP에 섞이지 않게 한다.

`make package`는 package 생성 전에 별도 축약 검증을 만들지 않고 `make validate`와 같은 전체 정적 gate를 수행한다. 생성된 ZIP은 제외 대상 파일과 환경 파일이 포함되지 않았는지 다시 검사한다.

Release ZIP은 배포에 필요한 artifact와 `tests/`를 함께 담는다 -- `make first-run`이 `make test`를 배포 전 게이트로 부르므로, 받는 쪽이 같은 버전의 테스트로 검증할 수 있어야 한다. 테스트 구조와 release gate의 관계는 [8. 테스트와 검증](./08_testing_validation.md), 실제 배포 절차는 [10. 배포](./10_deployment.md)에서 설명한다.

---

## 7.8 전체 초기화와 재빌드

새로운 개발 환경이나 전체 runtime build 입력을 한 번에 준비할 때는 bootstrap 명령을 사용한다.

이 절차는 Unified vLLM CUDA image와 NVIDIA full-stack을 포함하므로 Bash 4+, native
Linux amd64 Docker daemon과 접근 가능한 NVIDIA GPU를 시작 전에 요구한다. OS 이름을
지원 여부의 대리값으로 사용하지 않는다. macOS 개발 환경 준비에는 `make setup-dev`를
사용하고, 일반 application image만 확인하려면 `make build-image`를 사용한다.

```bash
HF_TOKEN=hf_xxx make first-run
```

`make first-run`은 다음 작업을 순서대로 수행한다.

```text
지원 Python 확인
   ↓
.venv 재생성
   ↓
Lock-file Dependency 설치
   ↓
Compose .env 준비
   ↓
Authentication / Exposure 적용
   ↓
make validate
   ↓
make test
   ↓
Platform Image Build
   ↓
Unified vLLM Image Build
   ↓
로컬 Unified Image ID 고정
   ↓
Prompt Risk Runtime Config 확인
```

`HF_TOKEN`을 명령에 전달하면 bootstrap이 해당 값을 `.env`에 반영한다.

`make first-run`은 전체 bootstrap workflow를 사용한다.

Bootstrap 완료 후 full-stack은 다음 순서로 실행한다.

```bash
make compose-up
make ready-full
```

환경변수 보존과 `.env` 동기화 규칙은 [5.10 환경 파일](./05_configuration.md#510-환경-파일)을 참고한다.

---

## 7.9 빌드 결과 확인

작업별 확인 방법은 다음과 같다.

| 작업 | 확인 명령 | 확인 범위 |
|---|---|---|
| app-only | `make ready-local` | Gateway / Risk Adapter health |
| full-stack | `make ready-full` | Gateway readiness + vLLM + inference path |
| Platform Image | `make build-image` | Docker build + application import |
| 전체 Build Gate | `make build` | validate + test + Platform Image |
| Compose config | `make compose-config` | effective Compose config rendering |
| 서비스 상태 | `make status` | 현재 service / process 상태 |
| Release ZIP | `make package` | package validation + ZIP 생성 |

`make compose-up`은 기동 전에 Docker, GPU, host port, secret을 preflight로 확인한다.

실행 중인 stack의 기본 상태를 확인하려면 다음 명령을 사용한다.

```bash
make status
```

Compose 환경에서 로그가 필요한 경우에는 다음 명령을 사용할 수 있다.

```bash
make compose-logs
```

상세 runtime 검증과 장애 진단은 [8. 테스트와 검증](./08_testing_validation.md), [11. 관측성과 장애 대응](./11_observability.md)에서 다룬다.

---

## 7.10 작업별 빠른 참조

| 목적 | 명령 |
|---|---|
| app-only 환경 생성 | `make init-env-local` |
| app-only 시작 | `make start` |
| app-only 확인 | `make ready-local` |
| app-only 종료 | `make stop` |
| full-stack 환경 생성 | `make init-env-compose` |
| full-stack 시작 | `make compose-up` |
| full-stack 확인 | `make ready-full` |
| full-stack 종료 | `make compose-down` |
| 정적 검증 | `make validate` |
| 테스트 | `make test` |
| Platform 전체 build gate | `make build` |
| Platform image만 build | `make build-image` |
| Unified vLLM image build | `make build-vllm-unified-image` |
| 전체 bootstrap | `make first-run` |
| Release ZIP 생성 | `make package` |

---

## 7.11 관련 문서와 Source of Truth

| 영역 | 문서 / 파일 | 역할 |
|---|---|---|
| Runtime 실행 구조 | [4. 실행 환경과 모드](./04_runtime_modes.md) | app-only / full-stack, network, readiness |
| 설정 관리 | [5. 설정 체계와 Source of Truth](./05_configuration.md) | 환경변수, image, runtime 설정 |
| 모델 운영 | [6. 모델 운영](./06_model_operations.md) | Main Model start / stop / switch |
| 테스트 | [8. 테스트와 검증](./08_testing_validation.md) | validate, test, runtime validation |
| CI build | [9. CI/CD](./09_cicd.md) | registry build / push / digest |
| 배포 | [10. 배포](./10_deployment.md) | release artifact 배포와 rollback |
| Make entry point | `Makefile` | 로컬 개발·빌드 명령 |
| Platform image | `Dockerfile` | application / control-plane image |
| Platform build script | `scripts/build/build_platform_image.sh` | 로컬·CI 공통 Platform build |
| Unified vLLM build config | `configs/vllm_unified_build.yaml` | target platform, base image와 compatibility pin |
| Unified vLLM Dockerfile | `ops/images/vllm-unified/Dockerfile` | derived vLLM runtime image |
| Bootstrap | `scripts/build/bootstrap.sh` | 개발 환경과 build artifact 전체 준비 |
| Release package | `scripts/build/package_release.sh` | 배포용 ZIP 생성 |
