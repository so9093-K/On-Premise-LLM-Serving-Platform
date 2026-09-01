# 10. 배포

배포는 CI에서 생성된 이미지와 대상 서버 설정을 적용하고, 서비스 실행 상태를 검증하는 과정이다.

```text
배포 방식 결정
     ↓
새 Release 준비
     ↓
필요한 서비스 갱신
     ↓
Health / Readiness 확인
     ↓
배포 완료
```

CI Pipeline과 이미지 생성 과정은 [9. CI/CD](./09_cicd.md), 실행 구조와 네트워크 공개 방식은 [4. 실행 환경과 모드](./04_runtime_modes.md)에서 설명한다.

---

## 10.1 배포 개요

배포 방식은 `rolling`과 `full`로 구분한다.

| 배포 방식 | 적용 기준 | 적용 범위 | 완료 기준 |
|---|---|---|---|
| `rolling` | Gateway, Risk Adapter, Admin Sidecar 등 애플리케이션 변경 | 필요한 애플리케이션 서비스와 관련 모니터링 설정 갱신 | Gateway `/health` |
| `full` | Main Model 설정, Compose, Unified vLLM 등 모델 실행 환경 변경 | 모델 Runtime을 포함한 관련 서비스와 실행 상태 갱신 | `make ready-full` |

### Rolling

```text
Platform Image
     ↓
새 Release 준비
     ↓
Admin Sidecar 갱신
     ↓
Gateway + Risk Adapter 갱신
     ↓
Gateway /health
```

Rolling 배포는 실행 중인 모델 Runtime을 유지하면서 Gateway, Risk Adapter, Admin Sidecar 등 애플리케이션 서비스를 갱신한다.

### Full

```text
Platform Image
 + Runtime Configuration
 + Optional Unified vLLM Image
        ↓
새 Release 준비
        ↓
Main Model / Runtime 구성 계산
        ↓
필요한 서비스 갱신
        ↓
Gateway /health
        ↓
make ready-full
```

Full 배포는 애플리케이션과 모델 실행 환경의 변경을 함께 반영한다. 배포 과정에서는 현재 상태와 새 Release를 비교해 필요한 서비스만 갱신한다.

---

## 10.2 배포 방식 결정

기본 배포 방식은 `full`이다. Gateway, Risk Adapter, Admin Sidecar 등 애플리케이션 서비스만 변경된 경우 `rolling`을 사용할 수 있다.

| 변경 내용 | 배포 방식 |
|---|---|
| Gateway / Risk Adapter / Admin Sidecar 변경 | `rolling` |
| Main Model 프로파일 / Chat Template 변경 | `full` |
| Compose 구성 변경 | `full` |
| Unified vLLM 이미지 변경 | `full` |
| 최초 Release 배포 | `full` |
| 전체 Runtime 상태와 준비 상태를 함께 검증하는 배포 | `full` |

모델 실행 환경에 영향을 주는 변경이 포함되면 `full` 배포가 적용된다.

`full` 배포 전환 판단에 사용하는 주요 파일은 다음과 같다.

- `ops/compose/full-stack.private-network.yaml`
- `configs/main_model_profiles.yaml`
- `configs/gemma4_chat_template.jinja`

이번 Pipeline에서 새 Unified vLLM digest가 생성된 경우에도 `full` 배포가 적용된다.

---

## 10.3 배포 입력

배포 입력은 **실행 이미지**, **대상 서버 환경 설정**, **Runtime 구성**으로 구성된다.

| 입력 | 역할 |
|---|---|
| Platform 이미지 | Gateway, Risk Adapter, Admin Sidecar에 적용할 버전 |
| Unified vLLM 이미지 | 새 모델 Runtime 이미지가 생성된 경우 적용할 버전 |
| 대상 `.env` | 서버별 실행 설정과 Secret 참조값 |
| 배포 방식 | `rolling` / `full` 결정 |
| Deploy Runtime Profile | Full 배포 직후 실행할 Secondary Runtime 구성 |
| Main Model 프로파일 / 상태 | Full 배포 시 시작할 Main Model 구성 |

Platform 이미지는 Registry의 고정된 digest로 전달된다.

```text
<registry>/platform@sha256:...
```

새 Unified vLLM 이미지가 생성된 Pipeline에서는 해당 digest도 함께 전달된다.

```text
<registry>/vllm-unified@sha256:...
```

새 Unified vLLM digest가 없는 Full 배포는 대상 서버에 저장된 Runtime 이미지 설정을 사용한다.

대상 서버에는 Docker/Compose, NVIDIA Runtime, Registry pull credential과 Main Model 로딩에 필요한 Hugging Face credential 또는 cache가 준비되어 있어야 한다.

환경 설정과 프로파일의 Source of Truth는 [5. 설정 체계와 Source of Truth](./05_configuration.md)를 참고한다.

---

## 10.4 배포 실행 흐름

배포는 새 Release를 별도 디렉터리에 준비한 뒤, 설정 확인과 서비스 갱신을 순서대로 수행한다.

```text
1. Release 준비
      ↓
2. 이미지 / Runtime 사전 확인
      ↓
3. 환경 설정 적용
      ↓
4. Compose 설정 검증
      ↓
5. Release 활성화
      ↓
6. 필요한 서비스 갱신
      ↓
7. Health / Readiness 확인
```

### 1. Release 준비

CI Runner는 repository source를 새 Release 디렉터리에 동기화한다.

```text
Repository Source
       ↓
releases/<release-id>
```

CI에서는 commit SHA를 Release ID로 사용한다. `.env`, Runtime state, model cache는 배포 루트의 공유 경로를 사용한다.

### 2. 이미지와 Runtime 사전 확인

서비스 적용 전에 다음 항목을 확인한다.

- Release ID와 배포 방식
- 모델 실행 환경에 영향을 주는 변경 여부
- Unified vLLM 빌드 입력과 생성된 이미지의 일치 여부
- Registry 이미지 pull 가능 여부
- 기존 환경 설정 백업 상태

Full 배포에서는 Platform 이미지와 필요한 vLLM Runtime 이미지의 Registry pull 가능 여부를 확인한다.

vLLM 빌드 구성이 변경된 배포에서는 해당 Pipeline에서 생성된 새 Runtime 이미지를 사용한다.

### 3. 환경 설정 적용

대상 `.env`를 백업한 뒤 새 Release ID와 이미지 참조값을 반영한다.

```text
PLATFORM_IMAGE=<platform digest>
DEPLOY_RELEASE_ID=<release id>
```

새 Unified vLLM 이미지가 있는 Full 배포에서는 공통 Runtime 이미지 설정도 함께 갱신한다.

배포 소스의 환경 템플릿에 새 키가 추가된 경우 `make sync-env`가 대상 `.env`에 해당 키를 동기화한다. 기존 운영 값은 유지된다.

`AUTH_MODE`가 전달된 배포에서는 해당 인증 프로파일을 적용하고, Runtime Secret 설정은 `make sync-runtime-secrets`에서 갱신한다.

### 4. 설정과 Compose 검증

동기화된 환경값으로 Gateway 설정을 확인하고, 실제 배포에 사용할 Compose 구성을 생성한다.

Full 배포에서는 다음 입력을 함께 반영한다.

- `EXPOSURE_MODE`
- 저장된 Main Model 실행 상태
- `main_model_profiles.yaml`
- Deploy Runtime Profile 또는 초기 중지 Runtime 설정

Base Compose에 네트워크 공개 설정, Main Model 시작 설정, 대상 `.env`를 반영해 실제 배포에 사용할 Compose 구성을 만든다.

### 5. Release 활성화

검증이 완료된 새 Release를 현재 애플리케이션 Release로 전환한다.

```text
current
  → releases/<new-release-id>
```

Full 배포에서는 Runtime이 참조하는 Release도 함께 전환한다.

```text
runtime-current
  → releases/<new-release-id>
```

Full 배포는 선택된 Main Model 프로파일의 고정 model revision을 공유 Hugging Face cache에 준비한다.

### 6. 필요한 서비스 갱신

Full 배포는 현재 실행 상태와 새 Release를 비교해 갱신이 필요한 서비스를 계산한다.

```text
현재 실행 상태
      +
새 Release
      ↓
변경 사항 확인
      ↓
갱신 대상 계산
      ↓
필요한 서비스 갱신
```

주요 판단 기준은 다음과 같다.

| 변경 | 적용 대상 |
|---|---|
| 실행 중인 이미지와 새 이미지가 다름 | 해당 서비스 |
| 필요한 서비스가 실행 중이지 않음 | 해당 서비스 |
| Main Model 프로파일 / Chat Template 변경 | `main-llm-vllm` |
| Compose 정의 변경 | 관련 Compose 서비스 |
| 이전 Release를 참조하는 컨테이너 | 해당 서비스 |

갱신 대상 서비스는 `docker compose up -d --no-deps`로 적용한다.

Rolling 배포는 다음 순서로 애플리케이션 서비스를 갱신한다.

1. `admin-sidecar`
2. `gateway`, `risk-adapter`

Admin Sidecar를 먼저 갱신한 뒤 Gateway와 Risk Adapter를 적용한다.

Prometheus, Grafana, Loki, Alloy의 설정 파일이 변경된 Release에서는 해당 서비스도 함께 갱신한다.

---

## 10.5 Runtime 프로파일 적용

Full 배포에서는 Deploy Runtime Profile을 기준으로 배포 직후 실행할 Secondary Runtime을 결정한다.

```text
Deploy Runtime Profile
        ↓
배포 직후 실행 상태 결정
        ↓
필요한 Runtime 시작
```

| 프로파일 | 배포 직후 구성 |
|---|---|
| `main_only` | Embedding / Korean Embedding / Prompt Risk는 초기 중지 상태로 구성 |
| `retrieval_ready` | Prompt Risk를 초기 중지 상태로 구성 |

`DEPLOY_DEFERRED_RUNTIMES`가 지정된 경우 해당 목록을 직접 적용한다.

초기 중지 상태의 Runtime은 Gateway에 `stopped`로 기록되며, 이후 시작 요청의 대상이 된다.

Main Model 프로파일과 Secondary Runtime의 시작/중지 운영은 [6. 모델 운영](./06_model_operations.md)에서 설명한다.

---

## 10.6 배포 완료 확인

배포 완료 기준은 적용된 서비스의 상태 확인과 실제 inference 경로 검증이다.

```text
서비스 적용
    ↓
Gateway Health
    ↓
Runtime Readiness
    ↓
Inference Smoke Test
    ↓
배포 완료
```

### Gateway 상태 확인

`RUN_READY_SMOKE=1`인 배포에서는 Gateway `/health` 성공 여부를 주기적으로 확인한다.

```text
Container Applied
      ↓
Gateway /health
      ↓
HTTP 200
```

기본 확인 URL은 대상 `.env`의 `GATEWAY_BIND_ADDR`와 `GATEWAY_PORT`를 기준으로 계산하며, `GATEWAY_HEALTH_URL`이 지정된 경우 해당 URL을 사용한다.

### 전체 Runtime 준비 상태 확인

Full 배포에서는 `make ready-full`로 전체 Runtime 준비 상태와 대표 API 동작을 확인한다.

```text
Gateway /health
      ↓
Gateway /ready
      ↓
Main Model Chat Serving
      ↓
Strict Smoke Test
```

`make ready-full`은 다음 경로를 확인한다.

- Gateway `/health`
- Gateway `/ready`와 의존 서비스 상태
- Main Model Chat 요청 처리 상태
- `/v1/models`
- Chat completion의 JSON Schema structured output
- Risk assessment
- General Embedding
- Korean Retrieval Embedding

배포 직후 중지 상태로 구성된 Runtime은 해당 readiness와 Smoke Test 대상에서 제외된다.

Full 배포는 `RUN_READY_FULL_SMOKE=1`을 사용하며, readiness 실패 시 `make compose-diagnostics`를 실행해 컨테이너와 Runtime 상태를 수집한다.

검증 단계의 전체 구분은 [8. 테스트와 검증](./08_testing_validation.md)을 참고한다.

---

## 10.7 실패와 복구

배포 실패가 발생하면 서비스 적용 여부에 따라 복구 절차가 달라진다.

### Release 적용 전 실패

다음 단계에서 오류가 발생하면 준비 중인 Release를 정리하고 현재 운영 상태를 유지한다.

- Release 요청 확인
- 모델 실행 환경 변경 여부 확인
- Unified vLLM 빌드 입력과 생성된 이미지 확인
- Registry 이미지 pull 사전 확인

### Release 적용 후 실패

환경 설정 또는 서비스 상태가 변경된 이후 오류가 발생하면 이전 Release를 복원한다.

```text
배포 실패
   ↓
이전 Environment 복원
   ↓
이전 Runtime 상태 복원
   ↓
이전 Release 활성화
   ↓
서비스 상태 복원
   ↓
Readiness 확인
```

복구 범위에는 다음 상태가 포함된다.

- 공유 `.env`
- 배포 과정에서 변경된 Gateway Runtime 상태
- 이전 Release 소스
- `current` 심볼릭 링크
- Full 배포의 `runtime-current` 심볼릭 링크
- 배포 과정에서 갱신된 서비스의 이전 Release 구성

Full 배포 복구는 새 Release와 이전 Release의 차이를 계산해 이전 상태에 필요한 서비스를 다시 적용한다.

자동 복구가 완료되지 않은 경우 배포 Job은 수동 복구가 필요한 상태로 종료하고 `.env` 백업 경로를 로그에 남긴다.

### 최초 배포

Release 디렉터리 기반 첫 배포는 `full` 방식으로 수행한다. 대상 `DEPLOY_PATH`에는 `.env`와 Docker/GPU 실행 환경이 준비되어 있어야 한다.

---

## 10.8 Release와 Runtime 구조

배포 대상 서버에서는 Release 소스와 공유 Runtime 데이터를 별도로 관리한다.

```text
DEPLOY_PATH/
│
├─ .env                         # 대상 서버 공통 환경 설정
├─ .runtime/                    # Gateway / Main Model Runtime 상태
├─ ops/compose/model_cache/     # 공유 model cache
│
├─ releases/
│   ├─ <release-id>/            # 배포 후 변경하지 않는 Release 소스
│   ├─ <release-id>/
│   └─ ...
│
├─ current -> releases/<id>     # 현재 애플리케이션 / Compose Release
└─ runtime-current -> releases/<id>
                                # 현재 Full Runtime Release
```

각 Release 디렉터리는 공통 파일을 심볼릭 링크로 연결한다.

```text
releases/<id>/.env
  → DEPLOY_PATH/.env

releases/<id>/.runtime
  → DEPLOY_PATH/.runtime

releases/<id>/ops/compose/model_cache
  → DEPLOY_PATH/ops/compose/model_cache
```

`current`는 애플리케이션과 Compose가 참조하는 Release를 나타낸다. Full 배포는 `runtime-current`도 새 Release로 전환하며, rolling 배포는 기존 Runtime Release를 유지한다.

### Release 보관

성공한 배포 이후 오래된 Release 디렉터리를 보관 정책에 따라 정리한다.

기본 보관 수는 다음 값으로 관리한다.

```text
RELEASES_TO_KEEP=5
```

현재 애플리케이션 Release와 현재 Runtime Release는 보관 대상에 유지된다. 성공한 배포에서는 임시 `.env` 백업과 Runtime 상태 백업을 정리하고 모니터링 설정의 적용 상태를 기록한다.

### 운영 중지와 재기동

배포 서버의 중지는 로컬 개발 종료와 다르다. Gateway와 모델 Runtime을 함께 멈추므로, GPU 호스트 작업이나 계획된 점검처럼 서비스 중단이 허용된 경우에만 실행한다. 코드나 이미지 변경을 반영하려는 목적이라면 수동 재기동 대신 CI/CD 배포를 사용한다.

항상 현재 Release 링크를 기준으로 실행한다. `releases/<id>`의 실제 경로에서 직접 실행하면 Compose가 다른 실행 컨텍스트로 인식할 수 있다.

```bash
cd -L /opt/acl-ai-gateway/current
make compose-down
```

이 명령은 Compose 컨테이너와 네트워크를 중지·제거한다. 공유 `.env`, `.runtime`, 모델 cache, Docker volume, Release 보관본은 삭제하지 않는다. `make clean-all`, 임의의 `docker rm`, cache 삭제는 운영 중지 절차에 포함하지 않는다.

점검이 끝난 뒤 같은 Release를 다시 올릴 때는 다음 순서로 실행한다.

```bash
cd -L /opt/acl-ai-gateway/current
make compose-up
make ready-full
```

`make compose-up`은 현재 `.env`, 노출 설정, 저장된 Main Model 부팅 프로필을 다시 확인한 뒤 Stack을 시작한다. `make ready-full`이 성공해야 Gateway뿐 아니라 필요한 Runtime이 준비된 상태로 본다. 중지·재기동 중 Compose 컨텍스트 오류나 Runtime 시작 실패가 나면 직접 컨테이너를 삭제하지 말고 `make compose-diagnostics` 결과와 배포 로그를 확인한다.

---

## 10.9 실패 시 확인 항목

배포 실패는 로그에 표시된 단계와 관련 입력을 기준으로 확인한다.

| 상황 | 주요 확인 항목 |
|---|---|
| Release 준비 실패 | SSH 권한, `DEPLOY_PATH`, Release ID |
| 대상 `.env` 누락 | 대상 서버 초기 구성과 `DEPLOY_PATH/.env` |
| Platform 이미지 pull 실패 | Platform digest, Registry Deploy Token, 네트워크 연결 |
| Unified vLLM 이미지 확인 실패 | Runtime 이미지 digest와 Registry 접근 여부 |
| vLLM 빌드 결과 불일치 | Unified vLLM 빌드 입력과 새 이미지 digest |
| Gateway 설정 검증 실패 | `.env`의 timeout, limit, Runtime 설정 조합 |
| 네트워크 공개 설정 실패 | `EXPOSURE_MODE`, exposure profile, override file |
| Main Model 시작 설정 실패 | 저장된 모델 상태와 `main_model_profiles.yaml` |
| Compose 설정 검증 실패 | `.env`, Compose interpolation, exposure/boot 설정 |
| Main Model cache 준비 실패 | Hugging Face credential, cache path, model revision 접근 권한 |
| 서비스 적용 실패 | 대상 서비스 이미지와 Docker/Compose 로그 |
| Gateway `/health` 실패 | Gateway 컨테이너와 애플리케이션 시작 로그 |
| `ready-full` 실패 | 의존 서비스 상태, vLLM health, Main Model gate, Smoke Test 결과 |
| 자동 복구 불완전 | 이전 Release, `.env` 백업, Release link, 서비스 상태 |

보다 상세한 로그와 운영 진단은 이후 운영/장애 대응 문서에서 다룬다.

---

## 10.10 주요 설정과 파일

### 주요 배포 설정

| 설정 | 역할 |
|---|---|
| `DEPLOY_MODE` | `full` / `rolling` 결정 |
| `DEPLOY_PATH` | Release와 공유 Runtime 데이터가 위치하는 배포 루트 |
| `DEPLOY_RUNTIME_PROFILE` | Full 배포의 Secondary Runtime 초기 상태 |
| `DEPLOY_DEFERRED_RUNTIMES` | 초기 중지 Runtime 직접 지정 |
| `GATEWAY_HEALTH_URL` | 배포 후 Gateway health 확인 URL 재정의 |
| `RUN_READY_SMOKE` | Gateway `/health` 확인 실행 |
| `RUN_READY_FULL_SMOKE` | Full 배포의 전체 Runtime 준비 상태 확인 |
| `RELEASES_TO_KEEP` | Release 디렉터리 보관 수 |

### 주요 파일

| 영역 | 파일 | 역할 |
|---|---|---|
| CI 배포 Job | `.gitlab-ci.yml` | `release` 브랜치의 수동 배포와 이미지 digest 전달 |
| 배포 진입점 | `scripts/ci/deploy_gitlab_compose.sh` | Release 준비, 환경 적용, 서비스 적용, 준비 상태 확인, 복구 |
| 배포 요청 정책 | `scripts/lib/deploy_request_policy.sh` | `full` / `rolling` 결정과 요청 조건 검증 |
| 서비스 재생성 정책 | `scripts/lib/deploy_recreate_policy.sh` | 변경 서비스와 모델 실행 환경 관련 설정 판별 |
| 배포 환경 처리 | `scripts/lib/deploy_env.sh` | 대상 `.env` 조회, 갱신, export |
| 초기 중지 Runtime 계산 | `scripts/runtime/deferred_runtimes.py` | Deploy Runtime Profile과 Runtime 상태 적용 |
| Bind-mounted 설정 정책 | `scripts/lib/bind_mounted_config.sh` | 갱신 대상 서비스와 경로를 Compose 정의에서 파생하고, 설정 변경 여부를 판정 |
| Base Compose | `ops/compose/full-stack.private-network.yaml` | Full-stack 서비스 구성 |
| 전체 Runtime 준비 상태 | `scripts/ops/ready_full.sh` | Gateway, 의존 서비스, Main Model, inference 경로 검증 |
| Smoke Test | `scripts/ops/smoke_test.sh` | 대표 API 요청/응답 확인 |

관련 문서:

- [4. 실행 환경과 모드](./04_runtime_modes.md)
- [5. 설정 체계와 Source of Truth](./05_configuration.md)
- [6. 모델 운영](./06_model_operations.md)
- [8. 테스트와 검증](./08_testing_validation.md)
- [9. CI/CD](./09_cicd.md)
