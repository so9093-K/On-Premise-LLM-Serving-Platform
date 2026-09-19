# Canonical Terminology

이 문서는 프로젝트의 사용자-facing 문서, Control Plane Console, API 설명과 새 설정 이름에 사용할
표준 용어를 정의한다. 코드·Compose·환경변수의 기존 식별자는 호환성 계약일 수 있으므로, 표시
용어와 안정 식별자를 구분한다.

## 원칙

1. **역할을 이름에 쓴다.** `helper`, `adapter`, `secondary`처럼 관계만 나타내는 이름보다
   `Gateway`, `Runtime Controller`, `Embedding Runtime`처럼 책임을 드러내는 이름을 쓴다.
2. **표시명과 식별자를 분리한다.** 사람이 읽는 이름은 개선할 수 있지만, API path·service key·env key는
   migration 없이 바꾸지 않는다.
3. **원하는 상태와 실제 상태를 구분한다.** `desired state`와 `observed state`를 섞지 않는다.
4. **검증 수준을 다른 상태 의미와 섞지 않는다.** Main Model은 기술적 `Compatibility`와
   실제 검증 근거인 `Qualification`을 별도 축으로 두고, Deployment Target은
   `Implementation Status`와 `Qualification`을 별도 축으로 둔다. Main Model config와
   Admin API는 같은 canonical 상태 vocabulary를 사용한다.
5. **위험한 동작은 효과를 설명한다.** `force`처럼 구현 중심 표현만 버튼에 노출하지 않고 실제 영향
   (예: 다른 runtime 자동 중지 허용)을 설명한다.
6. **영문 제품명은 `On-Premises`를 사용한다.** `On-Premise`는 새 문서·표시명에서 사용하지 않는다.

## Canonical terms

| Canonical term | 의미 | 기존/내부 식별자 |
|---|---|---|
| **On-Premises LLM Serving Platform** | 제품명 | repository: `On-Premises-LLM-Serving-Platform` |
| **Gateway** | 외부 API 진입점. 요청 검증, 인증, routing과 응답 처리를 담당 | `gateway` |
| **Control Plane** | runtime·configuration·model profile을 운영하는 관리 계층 | `/admin/*`, Console |
| **Control Plane Console** | Control Plane API를 same-origin으로 사용하는 first-party 운영자 UI | `/admin/console/` |
| **Configuration Plane** | operator-owned 설정의 metadata, effective value, Plan/Apply/Verify와 history를 관리하는 계층 | `/admin/configuration/*` |
| **Runtime Controller** | runtime lifecycle, Main Model 전환, reconciliation과 Docker 제어를 담당 | service ID `runtime-controller`, telemetry/module identifier `runtime_controller`, Python client `runtime_controller_client` / `RuntimeControllerClient` |
| **Model Runtime** | 실제 model inference/embedding/detection을 수행하는 실행 단위 | vLLM/MLX runtime |
| **Main Model Runtime** | `local-main` 요청을 수행하는 주 generation runtime | `main-llm-vllm` |
| **Embedding Runtime** | embedding과 dense retrieval scoring에 사용하는 runtime | `embedding-vllm` |
| **Korean Embedding Runtime** | 한국어 retrieval 기본 embedding runtime | `embedding-ko-vllm` |
| **Prompt Injection Detector Runtime** | prompt injection/leaking 신호를 생성하는 model runtime | Compose service `risk-prompt-vllm`, public model alias `risk-prompt`, operator env `PROMPT_INJECTION_DETECTOR_*` |
| **Risk Signal Service** | PII·Secret·Prompt detector 결과를 신호 계약으로 정규화. 최종 allow/block 정책은 소유하지 않음 | service ID `risk-signal-service`; Python/config identifier `risk_signal_service`; operator env `RISK_SIGNAL_SERVICE_*` |
| **Main Model Profile** | Main Model의 model revision, runtime image, command, capability와 request policy 조합 | `configs/main_model_profiles.yaml` |
| **Public Model Alias** | client가 고정적으로 사용하는 model 이름 | `local-main` |
| **Deployment Target** | platform/backend/lifecycle ownership 조합을 고르는 안정 설정 ID | `DEPLOYMENT_TARGET` |
| **Runtime Startup Profile** | 배포 직후 어떤 non-main runtime을 시작 상태로 둘지 정하는 preset | `RUNTIME_STARTUP_PROFILE`, `configs/deploy_profiles.yaml` |
| **Access Profile** | 사용자가 선택하는 접근 의도(local/private/edge) | `ACCESS_PROFILE` |
| **Desired State** | Control Plane이 수렴시키려는 runtime 상태 | `desired_state` |
| **Observed State** | 실제 container/runtime에서 관측한 상태 | `observed_runtime`, `container_status` |
| **Compatibility** | Main Model Profile이 현재 deployment/runtime 조합에서 기술적으로 가능한지 나타내는 축 | `compatibility.status` |
| **Implementation Status** | Deployment Target 자체가 실행 가능한 구현 상태인지 나타내는 축 | `implementation_status` |
| **Qualification** | 특정 Main Model 또는 Deployment Target의 실제 검증 근거가 충족됐는지 나타내는 축 | `qualification.status`, `qualification_status` |
| **Activity** | 최근 runtime/model/configuration 변경 기록을 모아 보는 Console 화면 | API object는 `operation` 유지 |
| **Verification Details** | apply/switch 후 실제 상태가 기대 상태와 일치했는지 확인한 정보 | 기존 UI 문구 operation evidence |

## 사용자-facing에서 피할 표현

| 피할 표현 | 이유 | 대신 사용 |
|---|---|---|
| **Admin Sidecar** | 독립 control service를 Kubernetes식 sidecar로 오해할 수 있음 | Runtime Controller |
| **Secondary Runtime** | 역할을 설명하지 못하고 Main과의 상대 관계만 표현 | Model Runtime 또는 구체 역할명 |
| **Risk Adapter** | 신호 생성/정규화 역할이 드러나지 않음 | Risk Signal Service |
| **risk-prompt** (표시명) | 탐지 대상이 불명확 | Prompt Injection Detector |
| **Deploy Runtime Profile** | deployment 전체 profile처럼 보임 | Runtime Startup Profile |
| **operation evidence** | 내부 영속성 구현 용어에 가까움 | Verification Details / Activity |
| **force** (단독 버튼) | 실제 영향이 드러나지 않음 | 필요한 runtime 자동 중지 허용 |
| **AUDIO_VLLM_IMAGE** (신규 이름으로 사용) | 현재 역할이 audio 전용이 아니라 Main Model profile image override임 | 신규 사용 금지. deployment-time/direct deploy read는 제거됐고 persistent key는 `sync-env` migration 입력으로만 남음 |

### Process inputs

- `RUNTIME_STARTUP_PROFILE`이 로컬 compose-up과 원격 full deploy의 유일한 startup profile input이다.
- `RUNTIME_PROFILE`과 `DEPLOY_RUNTIME_PROFILE` process alias는 제거됐다.
- `PACKAGE_NAME`은 release ZIP 파일명을 바꾸는 packaging process override이며 Runtime `.env` key가 아니다.

## Migration namespaces

legacy 유지 여부는 참조 수가 아니라 **실제 보존해야 할 계약**으로 결정한다.
영속 데이터 손실을 막기 위한 migration, 외부 표준 호환, 명시적으로 안정화한 공개 계약이 아니면
compatibility alias를 기본으로 만들지 않는다. 내부/process 식별자는 같은 release에서 모든 producer와
consumer를 함께 바꿀 수 있으면 직접 cutover한다. migration bridge가 필요한 경우에도 신규 runtime
계약으로 승격하지 않고 제거 조건을 명시한다.

| Legacy namespace | Canonical target | 현재 정책 |
|---|---|---|
| `MAIN_LLM_*` | `MAIN_MODEL_*` | runtime read alias는 제거됨. 기존 persistent `.env`의 legacy key는 `sync-env` migration 입력으로만 유지되며 `MAIN_LLM_MODEL`은 `MAIN_MODEL_ALIAS`로 이관 |
| `risk-adapter` / `risk_adapter` / `RISK_ADAPTER_*` | Risk Signal Service 계열 legacy identifier | active Python/config/process identifier는 `risk_signal_service`, operator env는 `RISK_SIGNAL_SERVICE_*`로 수렴. `RISK_ADAPTER_*`는 `sync-env` migration 입력/테스트/history에만 유지하며 공개 `/v1/risk/*` API는 그대로 유지 |
| `risk_prompt` / `RISK_PROMPT_*` | Prompt Injection Detector 계열 legacy identifier | runtime/config key는 `prompt_injection_detector`, operator env는 `PROMPT_INJECTION_DETECTOR_*`로 수렴. `risk_prompt`는 persisted runtime-state migration 입력에만 남고 `RISK_PROMPT_*`는 `sync-env` migration 입력으로만 유지. public model alias `risk-prompt`와 Compose service `risk-prompt-vllm`은 별도 service-identity migration 전까지 유지 |
| `admin-sidecar` / `admin_sidecar` | Runtime Controller 계열 legacy identifier | Python shim, Compose service ID, DNS, telemetry key migration이 모두 완료됨. 현재 identifier는 `runtime-controller` / `runtime_controller` |

migration이 완료되기 전에는 기존 식별자를 삭제하거나 새 target과 충돌하는 값을 자동 선택하지 않는다.
canonical과 legacy 값이 동시에 존재하면서 다르면 fail-closed를 기본으로 한다.

## Legacy and ambiguous env keys

다음 key는 이름만 보고 의미를 추측하면 잘못 쓰기 쉬우므로 별도 의미 계약으로 유지한다.

| Key | 실제 의미 | 주의 |
|---|---|---|
| `MAIN_MODEL_ALIAS` | client-facing **Public Model Alias**. 현재 기본값은 `local-main` | upstream model/checkpoint 이름이 아니다. 기존 `MAIN_LLM_MODEL`은 persistent env migration 입력으로만 처리 |
| `GATEWAY_HOST` | host에서 직접 실행하는 Gateway process의 listen address | Compose host publish address가 아니다 |
| `GATEWAY_BIND_ADDR` | Compose가 Gateway port를 host에 publish할 때 bind할 address | application process listen address와 구분 |
| `API_KEYS` | Gateway가 허용하는 Bearer key 집합 | server-side accepted credential set |
| `API_KEY` | smoke/ops client가 요청에 사용할 단일 Bearer key | 기본 생성 경로에서는 `API_KEYS`의 첫 key와 같지만 역할은 다르다 |
| `ADMIN_API_KEYS` | Admin API가 허용하는 Bearer key 집합 | server-side accepted credential set |
| `ADMIN_API_KEY` | 운영 도구가 사용할 단일 Admin credential이자 일부 내부 secret projection의 source | plural key set과 역할을 구분한다 |

## 식별자 변경 정책

기존 API field, endpoint, Compose service, environment key를 바꿀 때 기본 정책은 **canonical contract로 직접 cutover**하는 것이다.
compatibility layer는 다음 중 하나가 구체적으로 성립할 때만 둔다.

1. 기존 persistent state/config 값을 읽지 못하면 업그레이드 과정에서 데이터 또는 운영 의도가 손실된다.
2. OpenAI-compatible API처럼 외부 표준 호환 자체가 제품 기능이다.
3. 버전 정책에서 안정화했다고 명시한 공개 계약을 실제 외부 consumer가 사용한다.

이 경우에도 runtime dual-read를 영구 계약으로 만들지 않는다. 가능한 경우 `sync-env` 같은 migration 도구가
기존 값을 canonical 형태로 한 번 옮기고, bridge에는 제거 조건을 함께 기록한다. 단순히 "기존 사용자가 있을 수 있다"는
추측이나 참조 수가 많다는 이유만으로 alias를 유지하지 않는다.

사용자-facing 표시명과 안정 식별자는 각각 의미에 맞게 canonicalize한다. 현재 Runtime Controller는
표시명뿐 아니라 Compose service ID도 `runtime-controller`를 사용한다. 과거 `admin-sidecar`는 historical
ADR/CHANGELOG에서 당시 사실을 설명할 때만 보존한다. `/admin/*`은 Control Plane 관리 API namespace이고
옛 sidecar 이름의 호환 path가 아니다. `/v1/risk/*`도 Risk 도메인을 표현하는 현재 public API이며
`risk-adapter` 이름을 보존하기 위한 alias가 아니다.

API 오류 메시지·OpenAPI 설명/예제·CLI help·Console help·운영 설정의 description도
사용자-facing 표시 계약에 포함한다. 이 surface에서는 `Runtime Controller`,
`Risk Signal Service`, `Prompt Injection Detector Runtime` 같은 canonical term을 사용하고,
`runtime-controller`, `risk-signal-service`, `risk_signal_service`, `risk-prompt-vllm` 같은 값은 실제 identifier를 정확히
가리켜야 할 때만 code formatting과 함께 노출한다. Runtime Controller endpoint의 canonical
env는 `RUNTIME_CONTROLLER_URL`이며 `ADMIN_SIDECAR_URL`은 persistent env migration 입력으로만 남는다.
