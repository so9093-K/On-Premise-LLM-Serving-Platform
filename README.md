# AI 모델 서빙 플랫폼

GPU 기반 AI 모델을 **OpenAI-compatible API**로 제공하고, Chat, Embedding, Retrieval, Risk Detection, 모델 운영, 관측과 배포를 하나의 플랫폼에서 관리한다.

외부 애플리케이션은 Gateway를 통해 모델 기능을 사용한다. 모델 실행 환경은 Linux/NVIDIA의
vLLM 또는 Apple Silicon의 MLX-VLM을 사용하며, 실행 target에 맞는 lifecycle은 같은 로컬
명령으로 관리한다.

## 주요 기능

- OpenAI-compatible Chat / Embedding API
- 한국어 Retrieval
- Prompt 위험 탐지 / PII·Secret 위험 탐지
- Main Model 시작·중지·전환
- GPU 기반 vLLM 모델 실행
- Prometheus / Grafana / Loki 기반 관측
- 재현 가능한 애플리케이션 검증, 이미지 빌드와 GPU 배포

## 시스템 구성

Gateway를 중심으로 모델 Runtime, Risk 처리, 운영 제어와 관측 서비스가 연결된다.

![AI 모델 서빙 플랫폼 시스템 구성도](assets/ai_model_serving_system_architecture.jpg)

전체 구성과 서비스별 역할은 [시스템 구성](docs/03_system_components.md)에서 설명한다.

---

## 시작하기

### 로컬 플랫폼 실행

처음 한 번 실행 target을 선택한다. `setup`이 application 환경과 target별 `.env`를
생성하므로 template을 복사하거나 Mac endpoint를 직접 조립하지 않는다.

| Target | 요구사항 |
|---|---|
| `macos-metal-static` | Apple Silicon, Python 3.13.12, Docker |
| `linux-nvidia-dynamic` | Linux amd64, NVIDIA GPU/driver/Container Toolkit, Docker, Bash 4+ |
| `linux-nvidia-static` | Linux amd64, Docker, 외부 OpenAI-compatible Main endpoint (`MAIN_URL=...`) |

```bash
make setup TARGET=macos-metal-static
make build
HF_TOKEN=hf_xxx make prepare
make up
make status
```

Linux/NVIDIA에서는 첫 명령의 target만 바꾼다.

```bash
make setup TARGET=linux-nvidia-dynamic
make build
HF_TOKEN=hf_xxx make prepare
make up
make status
```

`build`는 선택 target에서 이 저장소가 소유한 image만 만들고, `prepare`는 선택된 Main
Model만 받는다. secondary model을 전부 다운로드하지 않으며, 이미 준비된 환경의 일반
기동에서는 둘 다 반복하지 않는다. 이후 명령은 `.env`의 target을 사용하므로 `TARGET`을 반복하지 않는다.
Main profile을 처음부터 바꾸려면 `make setup TARGET=... MODEL=...`로 지정한다.
허용 profile은 각 target이 가리키는 `configs/main_model_profiles.yaml` 또는
`configs/macos_mlx_runtime.yaml`이 소유한다.

종료:

```bash
make down
```

세부 build·runtime 명령은 장애 진단이나 artifact 유지보수 때만 `make help-all`에서
확인한다.

### 애플리케이션만 실행

Gateway와 Risk Adapter를 로컬 Python 프로세스로 실행한다. Python `>=3.12,<3.14`가 필요하며 Docker와 GPU는 필요하지 않다. `setup-dev`는 기존 `.venv`를 재사용하고 runtime 설정과 실행 중인 서비스를 변경하지 않는다.

```bash
make setup-dev
make check
make init-env-local
make up
make status
```

환경별 Python·Bash 준비와 진단 방법은 [로컬 개발과 빌드](docs/07_local_dev_build.md)에서 설명한다.

종료:

```bash
make down
```

실행 구조와 네트워크 공개 방식은 [실행 환경과 모드](docs/04_runtime_modes.md), 설정 항목은 [설정 체계](docs/05_configuration.md)에서 확인한다. 다른 Main Model과 secondary runtime 운영은 [모델 운영](docs/06_model_operations.md), Release package 적용은 [배포](docs/10_deployment.md)에서 다룬다.

---

## 기본 API 확인

Gateway 기본 주소는 `http://127.0.0.1:9400`이다. 인증이 적용된 환경에서는 해당 프로파일의 Bearer token을 함께 사용한다.

Gateway는 브라우저에서 API를 확인할 수 있는 Scalar 기반 API Reference를 제공한다. Endpoint, 요청 필드와 응답 구조를 확인한 뒤 같은 API를 직접 호출할 수 있다.

![Scalar API Reference](assets/screenshots/scalar_api_reference.jpg)

### 상태 확인

```bash
curl -s http://127.0.0.1:9400/health
```

### 모델 목록

```bash
curl -s http://127.0.0.1:9400/v1/models
```

### Chat 요청

전체 GPU 환경의 준비가 완료된 상태에서 Main Model에 요청을 보낸다.

```bash
curl -s http://127.0.0.1:9400/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "local-main",
    "messages": [
      {"role": "user", "content": "안녕하세요"}
    ],
    "max_tokens": 128
  }'
```

Embedding, Retrieval, Risk Detection, Streaming, 인증 방식과 전체 요청·응답 계약은 [API 인터페이스](docs/reference/api_reference.md)에서 확인한다. Gateway의 브라우저 API Reference와 OpenAPI 명세는 `/docs`, `/redoc`, `/openapi.json`에서 제공한다.

---

## 개발과 배포

로컬 개발자는 `make check`로 정적 계약과 결정론적 테스트를 함께 실행한다. CI도 내부적으로
같은 `validate`, `test` 단계를 사용한다. 운영 이미지는 clean commit에서 만들고 immutable
digest로 배포한다. 로컬 image는 변경 중인 코드를 확인하는 개발 산출물이며 운영 artifact를 대체하지 않는다.

현재 자동 검증과 미래 publish·deploy 연결 경계는 [자동화 경계](docs/09_cicd.md), Release 적용과 복구 절차는 [배포](docs/10_deployment.md)에서 설명한다.

---

## 주요 명령

| 목적 | 명령 |
|---|---|
| 최초 target 환경 준비 | `make setup TARGET=<id>` |
| target image 빌드 | `make build` (`make rebuild`는 cache 재사용 없이 재빌드) |
| 선택 Main Model 준비 | `HF_TOKEN=... make prepare` |
| 전체 시작 / 종료 | `make up` / `make down` |
| checkout 전체 종료 | `make down-all` |
| 프로젝트 로컬 상태 초기화 계획 | `make reset` |
| 통합 상태 확인 | `make status` |
| 개발 변경 검증 | `make check` |
| 고급·유지보수 명령 | `make help-all` |

개발 환경과 이미지 빌드는 [로컬 개발과 빌드](docs/07_local_dev_build.md), 검증 항목은 [테스트와 검증](docs/08_testing_validation.md)에서 설명한다.

---

## 프로젝트 구조

```text
src/        애플리케이션 코드
configs/    서비스·모델·GPU 설정
specs/      JSON Schema·OpenAPI
ops/        Compose·Runtime Image·모니터링
scripts/    Build·검증·배포·운영 도구
docs/       프로젝트 문서
assets/     아키텍처·문서 이미지
```

변경 영역과 함께 확인할 설정·검증·배포 범위는 [변경 가이드](docs/13_change_guide.md)에서 정리한다.

---

## 문서

| 문서 | 내용 |
|---|---|
| [문서 안내](docs/README.md) | 전체 문서 구성과 읽기 순서 |
| [API 인터페이스](docs/reference/api_reference.md) | API 계약, 요청·응답, 인증, 예제 |
| [vLLM Container 실행 가이드](docs/reference/vllm_container_guide.md) | vLLM Container 직접 실행과 API 요청 |
| [설정 체계](docs/05_configuration.md) | 설정 구조와 적용 방식 |
| [자동화 경계](docs/09_cicd.md) | 현재 GitHub 검증과 미래 publish·deploy 연결 원칙 |
| [배포](docs/10_deployment.md) | Release 적용과 실패 복구 |
| [관측성](docs/11_observability.md) | 요청, Runtime, GPU, 로그 관측 |
| [운영 관리 및 장애 대응](docs/12_operations.md) | 운영 점검과 장애 진단·복구 |
| [변경 가이드](docs/13_change_guide.md) | 변경 영향과 검증·배포 범위 |
| [부록](docs/appendix.md) | 용어, 서비스·포트, 주요 명령, 주요 경로 |

---

## 버전과 변경 이력

현재 버전은 [`VERSION`](VERSION)을 기준으로 관리한다. 변경 이력은 [CHANGELOG](CHANGELOG.md)에서 확인한다.
