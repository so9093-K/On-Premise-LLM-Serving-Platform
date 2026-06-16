# 테스트 전략

테스트는 validator와 생성 산출물 검사를 먼저 두고, pytest는 source-of-truth invariant와
핵심 decision function을 검증하는 쪽으로 유지한다. 운영 환경을 실제로 쓰는 검증은
정적 release gate와 분리한다.

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
정적 release gate나 기본 pytest에 섞지 않는다.

## Runtime validation / 런타임 검증

`scripts/validation/runtime_validation.py`는 실제 live service와 vLLM endpoint를 호출한다.
Docker/GPU가 없는 환경에서는 `--config-only`로 설정 정합성만 확인한다. Docker compose,
GPU 표시, live vLLM 증빙은 `runtime`, `docker`, `gpu` 계층이며 명시적으로 실행한다.

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
| `tests/contract/test_risk_assessment_response_schema.py` | D1-D5 JSON Schema 통과, span_count, family consistency, forbidden field, A1/A2 기존 동작 |

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
- 과거 버그 이름을 영구화하지 않는가?
- source-of-truth invariant 또는 핵심 decision function을 검증하는가?
- 실패 메시지가 운영자에게 원인과 수정 경로를 알려주는가?

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
- `slow`, `runtime`, `docker`, `gpu` marker 분리

## Pytest marker와 명령

`pyproject.toml`은 `unit`, `contract`, `governance`, `smoke`, `runtime`, `docker`, `gpu`,
`slow` marker를 등록하고 strict marker 검사를 켠다. `tests/conftest.py`는 현재 디렉터리
구조에서 `tests/unit/`과 `tests/contract/`에 계층 marker를 부여한다. live dependency가
생기는 테스트는 marker를 명시한다.

| 목적 | 명령 | 포함 |
|---|---|---|
| 빠른 개발 루프 | `make test` | 결정론적 pytest, `slow/runtime/docker/gpu` 제외 |
| 전체 결정론적 pytest | `make test-full` | `runtime/docker/gpu` 제외, `slow` 포함 |
| 정적 릴리스 gate | `make release-check` | source drift, docs/env/compose/runtime config-only |
| 정적 gate + 결정론적 pytest | `make release-check-full` | `release-check` + `make test-full` 상당 |
| live 운영 증빙 | `make runtime-validate` | live service, Docker/GPU/vLLM 환경 |

## Release gate 역할

`make release-check`는 source-of-truth drift, generated artifact drift, docs exposure
semantic drift, env contract, OpenAPI snapshot, runtime config-only 정합성을 확인한다.
Docker/GPU는 필요하지 않다.

`make release-check-full`은 정적 gate 뒤 deterministic pytest를 실행한다. live GPU나
live vLLM을 가정하지 않는다.

`make runtime-validate`는 live services와 Docker/GPU/vLLM 상태를 운영 증빙으로 남기는
별도 단계다.
