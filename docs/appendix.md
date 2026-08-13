# Appendix

부록은 프로젝트에서 자주 참조하는 용어, 서비스와 포트, 주요 명령, Source of Truth, 주요 경로를 한 곳에 정리한다. 각 항목의 상세 동작과 운영 절차는 관련 본문 문서에서 설명한다.

---

## A. 용어 정리

| 용어 | 의미 | 관련 문서 |
|---|---|---|
| Gateway | 외부 API 요청의 진입점. 요청 검증, 인증, 모델 호출 조정과 응답 처리를 담당한다. | [3. 시스템 구성](./03_system_components.md) |
| Main Model Runtime | Chat inference를 수행하는 vLLM Runtime. | [6. 모델 운영](./06_model_operations.md) |
| Admin Sidecar | Main Model Runtime의 시작, 중지, 전환과 Docker lifecycle을 관리하는 내부 서비스. | [3. 시스템 구성](./03_system_components.md), [6. 모델 운영](./06_model_operations.md) |
| Secondary Runtime | Embedding, 한국어 Embedding, Prompt Risk 등 Main Model 외 모델 Runtime. | [4. 실행 환경과 모드](./04_runtime_modes.md) |
| Risk Adapter | ~~PII·Secret 위험 탐지~~와 Prompt Risk 신호를 제공하는 서비스. PII와 Secret은 내부 detector를 사용하고 Prompt Risk는 별도 vLLM Runtime을 호출한다. | [3. 시스템 구성](./03_system_components.md) |
| Main Model Profile | Main Model을 어떤 모델과 Runtime 설정으로 실행할지 정의하는 프로파일. | [5. 설정 체계와 Source of Truth](./05_configuration.md), [6. 모델 운영](./06_model_operations.md) |
| Deploy Runtime Profile | Full 배포 후 Secondary Runtime의 초기 실행 상태를 정의하는 프로파일. | [5. 설정 체계와 Source of Truth](./05_configuration.md), [10. 배포](./10_deployment.md) |
| Exposure Profile | 실행된 서비스 중 Host에 공개할 대상을 정의하는 프로파일. | [4. 실행 환경과 모드](./04_runtime_modes.md), [5. 설정 체계와 Source of Truth](./05_configuration.md) |
| Auth Profile | Gateway, Admin API, 내부 서비스의 인증 정책 조합을 정의하는 프로파일. | [5. 설정 체계와 Source of Truth](./05_configuration.md) |
| Readiness | 서비스와 필요한 모델 Runtime이 실제 요청을 처리할 준비가 된 상태. | [8. 테스트와 검증](./08_testing_validation.md) |
| Smoke Test | 대표 API 요청을 실제로 실행해 주요 요청 경로를 확인하는 검증. | [8. 테스트와 검증](./08_testing_validation.md) |
| Runtime Validation | GPU, vLLM, 모델 Runtime 등 실제 실행 환경을 확인하는 검증. | [8. 테스트와 검증](./08_testing_validation.md), [12. 운영 관리 및 장애 대응](./12_operations.md) |
| Platform Image | Gateway, Risk Adapter, Admin Sidecar 애플리케이션을 실행하는 Container Image. | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| Unified vLLM Image | Main Model, Embedding, Prompt Risk Runtime이 공유하는 vLLM 기반 Runtime Image. | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| Image Digest | Registry에 저장된 Container Image 내용을 고유하게 식별하는 `sha256` 값. CI/CD와 배포에서 실제 Image version을 고정하는 데 사용한다. | [9. CI/CD](./09_cicd.md), [10. 배포](./10_deployment.md) |
| Release | 배포에 사용할 소스, 설정, Runtime 정보를 독립된 디렉터리에 준비한 배포 단위. | [10. 배포](./10_deployment.md) |
| Source of Truth | 특정 설정이나 계약의 기준이 되는 코드 또는 설정 파일. | [5. 설정 체계와 Source of Truth](./05_configuration.md) |
| Generated Artifact | Source of Truth를 기준으로 스크립트가 생성하는 Runtime/Compose/OpenAPI 관련 파일. | [5. 설정 체계와 Source of Truth](./05_configuration.md) |

---

## B. 서비스와 포트

서비스 식별자와 기본 포트는 `configs/services.yaml`을 기준으로 한다. Host 공개 범위는 현재 `EXPOSURE_MODE`와 `configs/exposure_profiles.yaml`에 따라 결정된다.

### Application / Model Runtime

| 서비스 | Compose 서비스 | Container Port | 기본 Host Port | 역할 |
|---|---|---:|---:|---|
| Gateway | `gateway` | `9400` | `9400` | 외부 API 진입점 |
| Main Model Runtime | `main-llm-vllm` | `9401` | `9401` | Chat inference |
| Embedding Runtime | `embedding-vllm` | `9402` | `9402` | 일반 Embedding |
| Prompt Risk Runtime | `risk-prompt-vllm` | `9403` | `9403` | Prompt Risk inference |
| Risk Adapter | `risk-adapter` | `9405` | `9405` | ~~PII·Secret 위험 탐지~~, Prompt Risk 신호 처리 |
| Korean Embedding Runtime | `embedding-ko-vllm` | `9406` | `9406` | Retrieval용 한국어 Embedding |
| Admin Sidecar | `admin-sidecar` | `8080` | - | Main Model Runtime lifecycle 관리. Compose 내부에서 사용 |

### Monitoring

| 서비스 | Compose 서비스 | Container Port | 기본 Host Port | 역할 |
|---|---|---:|---:|---|
| Prometheus | `prometheus` | `9090` | `9410` | Metrics 저장 및 조회 |
| Grafana | `grafana` | `3000` | `9411` | Metrics / Logs Dashboard |
| DCGM Exporter | `dcgm-exporter` | `9400` | `9412` | GPU Metrics 수집 |
| cAdvisor | `cadvisor` | `8080` | `9413` | Container 자원 Metrics |
| Loki | `loki` | `3100` | `9414` | Log 저장 및 조회 |
| Alloy | `alloy` | - | - | Container/Application Log 수집 및 Loki 전달 |

`private_network`에서는 Gateway와 Grafana가 Host에 공개되며, 모델 Runtime과 운영용 backend는 Compose 내부 네트워크에서 사용한다. `master_open`에서는 진단과 사내망 운영을 위해 더 많은 서비스가 Host에 공개된다.

상세 네트워크 구성은 [4. 실행 환경과 모드](./04_runtime_modes.md)를 참고한다.

---

## C. 주요 명령

### 개발과 실행

| 목적 | 명령 | 관련 문서 |
|---|---|---|
| app-only 환경 초기화 | `make init-env-local` | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| app-only 시작 | `make start` | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| app-only 종료 | `make stop` | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| app-only 상태 확인 | `make ready-local` | [4. 실행 환경과 모드](./04_runtime_modes.md) |
| full-stack 환경 초기화 | `make init-env-compose` | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| full-stack 시작 | `make compose-up` | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| full-stack 종료 | `make compose-down` | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| full-stack 준비 상태 확인 | `make ready-full` | [8. 테스트와 검증](./08_testing_validation.md) |

### 검증과 테스트

| 목적 | 명령 | 관련 문서 |
|---|---|---|
| 설정·계약 정적 검증 | `make validate` | [8. 테스트와 검증](./08_testing_validation.md) |
| 자동화 테스트 | `make test` | [8. 테스트와 검증](./08_testing_validation.md) |
| 대표 API 요청 확인 | `make smoke` | [8. 테스트와 검증](./08_testing_validation.md) |
| GPU / vLLM Runtime 검증 | `make runtime-validate` | [8. 테스트와 검증](./08_testing_validation.md) |
| 모델 설정 검증 | `make model-validate` | [13. 변경 가이드](./13_change_guide.md) |
| Hugging Face / Main Model 설정 확인 | `make hf-config-check` | [6. 모델 운영](./06_model_operations.md) |

### 빌드와 패키징

| 목적 | 명령 | 관련 문서 |
|---|---|---|
| 검증 + 테스트 + Platform Image Build | `make build` | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| Platform Image Build | `make build-image` | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| Unified vLLM Image Build | `make build-vllm-unified-image` | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| Release ZIP 생성 | `make package` | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| 전체 초기 구성 | `make first-run` | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| Runtime 생성 파일 갱신 | `make render-runtime-assets` | [5. 설정 체계와 Source of Truth](./05_configuration.md) |

### 모델 운영

| 목적 | 명령 | 관련 문서 |
|---|---|---|
| 모델 목록 확인 | `make model-list` | [6. 모델 운영](./06_model_operations.md) |
| 모델 상태 확인 | `make model-status` | [6. 모델 운영](./06_model_operations.md) |
| 모델 추가 계획 생성 | `make model-propose-add ID=... PORT=... ENDPOINT=... UPSTREAM=... ROLE=...` | [13. 변경 가이드](./13_change_guide.md) |
| 모델 제거 계획 생성 | `make model-propose-remove ID=...` | [13. 변경 가이드](./13_change_guide.md) |
| vLLM 실행 명령 확인 | `make vllm-commands` | [6. 모델 운영](./06_model_operations.md) |

### 운영과 진단

| 목적 | 명령 | 관련 문서 |
|---|---|---|
| 현재 상태 확인 | `make status` | [12. 운영 관리 및 장애 대응](./12_operations.md) |
| Compose 구성 확인 | `make compose-config` | [4. 실행 환경과 모드](./04_runtime_modes.md) |
| Compose 상태·로그 진단 | `make compose-diagnostics` | [12. 운영 관리 및 장애 대응](./12_operations.md) |
| full-stack 로그 조회 | `make compose-logs` | [12. 운영 관리 및 장애 대응](./12_operations.md) |
| app-only 로그 조회 | `make logs` | [12. 운영 관리 및 장애 대응](./12_operations.md) |
| 인증 상태 확인 | `make auth-status` | [5. 설정 체계와 Source of Truth](./05_configuration.md) |
| 인증 진단 | `make auth-doctor` | [12. 운영 관리 및 장애 대응](./12_operations.md) |
| 서비스 노출 상태 확인 | `make exposure-status` | [4. 실행 환경과 모드](./04_runtime_modes.md) |

전체 Make target은 `make help`에서 확인할 수 있다.

---

## D. Source of Truth

기준 파일 목록과 선언 정책·생성물·실제 운영 상태의 경계는 [5. 설정 체계와 Source of Truth](./05_configuration.md)만을 기준으로 한다. 이 부록에는 같은 표를 복제하지 않는다.

빠른 탐색은 다음 링크를 사용한다.

- 모델 실행 profile: [6. 모델 운영](./06_model_operations.md)
- API 계약: [API Reference](./reference/api_reference.md)
- 배포·CI: [9. CI/CD](./09_cicd.md), [10. 배포](./10_deployment.md)

---

## E. 주요 경로

| 영역 | 주요 위치 | 역할 |
|---|---|---|
| API Endpoint | `src/ai_model_serving/api/routers/` | Gateway와 Admin API의 endpoint 정의 |
| Request / Response | `src/ai_model_serving/contracts/` | 요청·응답 모델과 application contract |
| Application | `src/ai_model_serving/` | Gateway, Risk Adapter, Admin Sidecar와 공통 application logic |
| Main Model Control | `src/ai_model_serving/main_model/` | Main Model 상태, 전환, Docker Runtime 제어 로직 |
| 설정 | `configs/` | 모델, 서비스, GPU, 인증, 노출, 배포 정책 |
| JSON Schema | `specs/schemas/` | API schema와 validation contract |
| OpenAPI | `specs/openapi.*.yaml` | 외부 API specification |
| Compose | `ops/compose/` | full-stack Container topology와 Compose 구성 |
| Runtime Image | `ops/images/` | Platform에서 사용하는 Runtime Image 정의 |
| Monitoring | `ops/prometheus/`, `ops/grafana/`, `ops/loki/`, `ops/alloy/` | Metrics / Logs 수집과 Dashboard 구성 |
| Build Script | `scripts/build/` | Container Image build와 release package 생성 |
| Compose Script | `scripts/compose/` | Compose 실행, 구성 확인, diagnostics |
| Deployment Script | `scripts/ci/deploy_gitlab_compose.sh` | GitLab 배포 실행과 Release 적용·복구 |
| Validation Script | `scripts/validation/` | 정적 검증과 Runtime 검증 |
| Operations Script | `scripts/ops/` | Readiness, smoke test 등 운영 확인 |
| Reports | `scripts/reports/` | 운영 상태와 Runtime 검증 자료 생성 |
| API Reference | `docs/reference/api_reference.md` | API 사용 방법, 요청·응답, 오류와 제약 설명 |
| 모델 참고 자료 | `docs/reference/models/` | upstream 모델 사양, 라이선스, 알려진 제약 |
| Screenshots | `assets/screenshots/` | GitLab, Grafana, Request Log 등 문서용 화면 |

변경 작업별 영향 범위는 [13. 변경 가이드](./13_change_guide.md)에서 정리한다.

---

## F. 문서 연결

| 주제 | 문서 |
|---|---|
| 프로젝트 개요 | [1. Overview](./01_overview.md) |
| 요청 처리 흐름 | [2. Request Flow](./02_request_flow.md) |
| 시스템 구성 | [3. 시스템 구성](./03_system_components.md) |
| 실행 환경과 모드 | [4. 실행 환경과 모드](./04_runtime_modes.md) |
| 설정 | [5. 설정 체계와 Source of Truth](./05_configuration.md) |
| 모델 운영 | [6. 모델 운영](./06_model_operations.md) |
| 개발과 빌드 | [7. 로컬 개발과 빌드](./07_local_dev_build.md) |
| 테스트와 검증 | [8. 테스트와 검증](./08_testing_validation.md) |
| CI/CD | [9. CI/CD](./09_cicd.md) |
| 배포 | [10. 배포](./10_deployment.md) |
| 모니터링 | [11. 관측성](./11_observability.md) |
| 운영 / 장애 대응 | [12. 운영 관리 및 장애 대응](./12_operations.md) |
| 변경 작업 | [13. 변경 가이드](./13_change_guide.md) |
| API 상세 | [API Reference](./reference/api_reference.md) |
