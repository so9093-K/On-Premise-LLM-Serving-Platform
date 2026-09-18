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
4. **검증 수준과 기술 호환성을 섞지 않는다.** 장기 canonical 상태는 기술적 `Compatibility`와
   실제 검증 근거인 `Qualification`을 별도 축으로 둔다. 기존 API의 `compatibility.status`는
   별도 migration 전까지 legacy projection으로 유지할 수 있다.
5. **위험한 동작은 효과를 설명한다.** `force`처럼 구현 중심 표현만 버튼에 노출하지 않고 실제 영향
   (예: 다른 runtime 자동 중지 허용)을 설명한다.
6. **영문 제품명은 `On-Premises`를 사용한다.** `On-Premise`는 새 문서·표시명에서 사용하지 않는다.

## Canonical terms

| Canonical term | 의미 | 기존/내부 식별자 |
|---|---|---|
| **On-Premises LLM Serving Platform** | 제품명 | repository: `On-Premises-LLM-Serving-Platform` |
| **Gateway** | 외부 API 진입점. 요청 검증, 인증, routing과 응답 처리를 담당 | `gateway` |
| **Control Plane** | runtime·configuration·model profile을 운영하는 관리 계층 | `/admin/*`, Console |
| **Runtime Controller** | runtime lifecycle, Main Model 전환, reconciliation과 Docker 제어를 담당 | `admin-sidecar` |
| **Model Runtime** | 실제 model inference/embedding/detection을 수행하는 실행 단위 | vLLM/MLX runtime |
| **Main Model Runtime** | `local-main` 요청을 수행하는 주 generation runtime | `main-llm-vllm` |
| **Embedding Runtime** | embedding과 dense retrieval scoring에 사용하는 runtime | `embedding-vllm` |
| **Korean Embedding Runtime** | 한국어 retrieval 기본 embedding runtime | `embedding-ko-vllm` |
| **Prompt Injection Detector Runtime** | prompt injection/leaking 신호를 생성하는 model runtime | `risk-prompt-vllm`, model alias `risk-prompt` |
| **Risk Signal Service** | PII·Secret·Prompt detector 결과를 신호 계약으로 정규화. 최종 allow/block 정책은 소유하지 않음 | `risk-adapter` |
| **Main Model Profile** | Main Model의 model revision, runtime image, command, capability와 request policy 조합 | `configs/main_model_profiles.yaml` |
| **Public Model Alias** | client가 고정적으로 사용하는 model 이름 | `local-main` |
| **Deployment Target** | platform/backend/lifecycle ownership 조합을 고르는 안정 설정 ID | `DEPLOYMENT_TARGET` |
| **Runtime Startup Profile** | 배포 직후 어떤 non-main runtime을 시작 상태로 둘지 정하는 preset | `RUNTIME_STARTUP_PROFILE`, `configs/deploy_profiles.yaml` |
| **Access Profile** | 사용자가 선택하는 접근 의도(local/private/edge) | `ACCESS_PROFILE` |
| **Desired State** | Control Plane이 수렴시키려는 runtime 상태 | `desired_state` |
| **Observed State** | 실제 container/runtime에서 관측한 상태 | `observed_runtime`, `container_status` |
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
| **likely** (표시 상태) | 무엇이 얼마나 probable한지 기준이 불명확 | 추가 검증 필요 / provisional 성격을 설명 |
| **AUDIO_VLLM_IMAGE** (신규 이름으로 사용) | 현재 역할이 audio 전용이 아니라 Main Model profile image override임 | Main Model profile image override 계열 이름 |

### Process-input aliases

- `RUNTIME_STARTUP_PROFILE`이 로컬 compose-up과 원격 full deploy의 canonical input이다.
- 기존 `RUNTIME_PROFILE`과 `DEPLOY_RUNTIME_PROFILE`은 migration 기간의 read compatibility alias다.
- canonical과 legacy 값이 동시에 존재하면서 다르면 실행을 중단한다.
- `PACKAGE_NAME`은 release ZIP 파일명을 바꾸는 packaging process override이며 Runtime `.env` key가 아니다.

## Migration namespaces

다음 식별자는 현재 자동화와 runtime key에 넓게 연결되어 있어 **즉시 제거하지 않는 compatibility namespace**다.
사용 범위가 크다는 사실은 migration 기간과 순서를 결정하지만 장기 canonical target 자체를 결정하지 않는다.
새 코드와 설정은 역할 중심 target을 우선하고, 기존 identifier는 ADR-0030의 alias → migration → validation →
legacy removal 순서를 따른다.

| 현재 namespace | 장기 canonical target | 현재 정책 |
|---|---|---|
| `MAIN_LLM_*` | `MAIN_MODEL_*` | operator env부터 단계적 migration. `MAIN_LLM_MODEL`은 `MAIN_MODEL_ALIAS`가 target |
| `risk_adapter` / `RISK_ADAPTER_*` | Risk Signal Service 계열 identifier | 공개 `/v1/risk/*` API는 그대로 유지 |
| `risk_prompt` / `RISK_PROMPT_*` | Prompt Injection Detector 계열 identifier | runtime/model 내부 identifier만 별도 migration |
| `admin-sidecar` / `admin_sidecar` | Runtime Controller 계열 identifier | Compose/module/client를 한 번에 바꾸지 않고 단계별 migration |

migration이 완료되기 전에는 기존 식별자를 삭제하거나 새 target과 충돌하는 값을 자동 선택하지 않는다.
canonical과 legacy 값이 동시에 존재하면서 다르면 fail-closed를 기본으로 한다.

## Legacy and ambiguous env keys

다음 key는 이름만 보고 의미를 추측하면 잘못 쓰기 쉬우므로 별도 의미 계약으로 유지한다.

| Key | 실제 의미 | 주의 |
|---|---|---|
| `MAIN_LLM_MODEL` | client-facing **Public Model Alias**. 현재 기본값은 `local-main` | upstream model/checkpoint 이름이 아니다 |
| `GATEWAY_HOST` | host에서 직접 실행하는 Gateway process의 listen address | Compose host publish address가 아니다 |
| `GATEWAY_BIND_ADDR` | Compose가 Gateway port를 host에 publish할 때 bind할 address | application process listen address와 구분 |
| `API_KEYS` | Gateway가 허용하는 Bearer key 집합 | server-side accepted credential set |
| `API_KEY` | smoke/ops client가 요청에 사용할 단일 Bearer key | 기본 생성 경로에서는 `API_KEYS`의 첫 key와 같지만 역할은 다르다 |
| `ADMIN_API_KEYS` | Admin API가 허용하는 Bearer key 집합 | server-side accepted credential set |
| `ADMIN_API_KEY` | 운영 도구가 사용할 단일 Admin credential이자 일부 내부 secret projection의 source | plural key set과 역할을 구분한다 |

## 식별자 변경 정책

기존 API field, endpoint, Compose service, environment key를 바꿀 때는 다음 순서를 따른다.

1. 새 canonical 식별자를 추가한다.
2. 기존 식별자를 읽기 호환 alias로 유지하고 deprecation을 문서화한다.
3. sync/migration 도구가 기존 persistent 값을 손실 없이 새 식별자로 이동한다.
4. 모든 생성물·검증·테스트가 canonical 식별자를 사용하도록 수렴한다.
5. 최소 한 호환 기간 뒤 legacy 식별자를 제거한다.

변경 비용이나 현재 참조 수는 target state를 낮추는 근거로 사용하지 않는다. 먼저 의미와 책임에 맞는
canonical target을 정하고, 그 다음에 migration 순서와 기간을 결정한다.

사용자-facing 표시명 변경은 안정 식별자 변경과 묶지 않는다. 예를 들어 Console과 문서는
**Runtime Controller**라고 표시하되 Compose service ID `admin-sidecar`는 별도 migration 전까지
그대로 유지한다.
