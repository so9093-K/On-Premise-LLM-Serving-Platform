# 테스트 전략

테스트는 validator와 생성 산출물 검사를 먼저 두고, pytest는 source-of-truth invariant와
핵심 decision function을 검증하는 쪽으로 유지한다. 운영 환경을 실제로 쓰는 검증은
실행 전 정적 검증과 분리한다.

## Unit test / 단위 테스트

`tests/unit/`은 settings, Gateway, Risk Adapter, upstream client, setup_env의 decision
function과 작은 projection을 검증한다. 외부 모델 서버 없이 fake client를 사용한다.

## Contract test / 계약 테스트

`tests/contract/`은 OpenAPI ref, JSON Schema, release hygiene, runtime policy처럼
프로세스 경계를 넘는 결정론적 계약을 검증한다.

## Governance / Source-of-truth validator

`scripts/validation/`의 validator와 generated artifact `--check` 명령이 drift 검사의
기본 계층이다. source YAML을 읽고 generated artifact, docs semantic, env contract,
compose override reference를 함께 판단할 수 있으면 개별 pytest 문자열 검사보다 validator를
강화한다. pytest governance test는 validator integration과 validator에 담기 어려운 핵심 policy
invariant만 남긴다.

Exposure policy처럼 service classification이 필요한 검증은 `configs/services.yaml`의
`categories`를 source-of-truth로 읽는다. profile validator가 category coverage와 누락을
판단하고, pytest는 작은 fixture로 category invariant만 확인한다.

## Smoke test / 스모크

`make smoke`는 배포된 서비스의 핵심 API 경로를 빠르게 확인한다. running service가 필요하므로
실행 전 정적 검증이나 기본 pytest에 섞지 않는다.

## Runtime validation / 런타임 검증

`scripts/validation/runtime_validation.py`는 실제 live service와 vLLM endpoint를 호출한다.
Docker compose, GPU 표시, live vLLM 증빙은 `runtime`, `docker`, `gpu` 계층이며
명시적으로 실행한다.

## Sensitive Data Protection 테스트 계층

PII Protection과 Secret Exposure Signal은 다음 테스트로 검증한다.

### Unit tests

| 파일 | 검증 대상 |
|---|---|
| `tests/unit/test_pii_protection_detector.py` | Korean custom recognizer, entity→D-code 매핑, span_count, 원문값 미포함, boolean consistency |
| `tests/unit/test_secret_exposure_detector.py` | regex 패턴, entropy-based generic candidate, D4/D5 매핑, 원문 secret 미포함 |
| `tests/unit/test_risk_data_exposure_contract.py` | `_validate_risk_category()` D1-D5, span_count 검증, forbidden field 차단 |
| `tests/unit/gateway/test_risk_forwarding_data_exposure.py` | Gateway PII/Secret forwarding, forbidden field rejection |

### Contract tests

| 파일 | 검증 대상 |
|---|---|

### 핵심 검증 불변식

- D1~D5 모든 코드가 schema를 통과한다.
- `data_exposure` family가 validator를 통과한다.
- `span_count`가 탐지 개수를 표현한다.
- 원문 PII/Secret 값이 response JSON에 포함되지 않는다.
- aggregate의 boolean consistency(`risk_detected == model_risk_detected == any(detected)`)가 유지된다.
- 기존 A1/A2 prompt detector 동작이 변경되지 않는다.
- D4 코드는 `strongest_code`에서 A1보다 우선한다.

## 새 테스트 추가 기준

새 pytest를 추가하기 전에 다음을 확인한다.

- 기존 validator가 source와 artifact를 함께 읽어 판단할 수 없는가?
- 기존 parameterized invariant로 표현할 수 없는가?
- 다음 변경에도 같은 손실을 막는 재사용 가능한 불변식인가? 특정 작업의 완료·삭제 여부나 운영 안내를 확인하는 일회용 검사는 만들지 않는다.
- 과거 버그 이름을 영구화하지 않는가?
- source-of-truth invariant 또는 핵심 decision function을 검증하는가?
- 실패 메시지가 운영자에게 원인과 수정 경로를 알려주는가?

아래 중 하나도 해당하지 않으면 테스트를 추가하지 않는다.

- 공개 API·권한·민감정보·데이터 손실처럼 사용자 또는 운영 손실을 막는다.
- 상태 전환, 재시도, 정책 판단처럼 핵심 decision function의 분기를 보호한다.
- 실제 장애의 재발을 막으며, 테스트 설명에 사고 또는 원인을 남긴다.

## 테스트 폐기 기준

다음 조건이면 삭제하거나 validator 하나로 합친다.

- `make validate` 또는 생성 artifact `--check`가 동일한 source/artifact 관계를 이미 검증한다.
- 현재 포트·모델 ID·기본값처럼 바뀔 수 있는 값을 그대로 암기할 뿐, 값이 바뀌었을 때의 손실을 설명하지 못한다.
- 구현 함수 호출 순서, private helper, 문자열 존재만 확인한다.
- 기본 품질 게이트에서 실행되지 않고, 실행 명령·담당자·릴리스 판단 기준도 없다.
- 실제 판정 능력 없이 “제거 후보”, “준비됨”처럼 상태만 설명하는 진단이다. 필요한 후보 환경에서 실제 config check 또는 smoke를 실행한다.

테스트를 지울 때는 "무엇이 이 위험을 대신 막는가"를 PR 설명 또는 커밋 메시지에 적는다.
테스트가 아닌 정적 검증으로 옮긴 경우에는 해당 validator가 단일 소유자가 된다.

## 배포 artifact 경계

테스트 소스는 build-time 품질 게이트의 입력이며 런타임 의존성이 아니다. `make package`가
만드는 배포 ZIP에는 `tests/`를 포함하지 않는다. 패키지 자체는 secret·개발 도구·테스트
소스·비결정적 timestamp가 없음을 생성 단계에서 스스로 검증한다.

## 금지 패턴

- 특정 과거 mode 이름을 직접 금지하는 테스트
- 문자열 포함만 검사하는 테스트
- source-of-truth를 읽지 않고 env key를 하드코딩하는 테스트
- 한 버그마다 한 테스트를 계속 추가하는 방식

## 권장 패턴

- validator 강화
- generated artifact `--check`
- policy invariant test
- decision function unit test
- live 환경이 필요한 증빙은 pytest marker가 아니라 명시적인 운영 명령으로 분리

## 실행 명령

기본 pytest에는 제외 marker가 없다. live service/GPU가 필요한 검증은 pytest에 섞지 않고
`make runtime-validate` 같은 명시적인 운영 명령으로 실행한다. Compose 파일의
source-of-truth projection처럼 Docker daemon 없이 검증 가능한 관계는 `make validate`의
정적 validator가 소유한다.

| 목적 | 명령 | 포함 |
|---|---|---|
| 실행 전 정적 검증 | `make validate` | source drift, docs/env/compose |
| 결정론적 pytest | `make test` | unit·contract pytest 전체 |
| live 운영 증빙 | `make runtime-validate` | live service, Docker/GPU/vLLM 환경 |

## 정적 검증 역할

`make validate`는 source-of-truth drift, generated artifact drift, docs exposure
semantic drift, env contract, OpenAPI snapshot 정합성을 확인한다.
Docker/GPU는 필요하지 않다.

`make test`는 unit·contract deterministic pytest를 실행한다. live GPU나 live vLLM을
가정하지 않는다.

`make runtime-validate`는 live services와 Docker/GPU/vLLM 상태를 운영 증빙으로 남기는
별도 단계다.
