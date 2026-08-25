# API 문서 화면 Reference

이 문서는 Gateway와 Risk Adapter가 제공하는 API 문서 화면의 현재 동작과 운영 경계를 설명한다. 요청·응답 계약과 호출 예시는 [API 인터페이스](./api_reference.md)를 기준으로 한다.

## 제공 경로

| 서비스 | Scalar | ReDoc | OpenAPI JSON |
|---|---|---|---|
| Gateway | `:9400/docs` | `:9400/redoc` | `:9400/openapi.json` |
| Risk Adapter | `:9405/docs` | `:9405/redoc` | `:9405/openapi.json` |

`/docs`는 Scalar UI이고, `/redoc`은 읽기 전용 문서 화면이다. 두 화면은 인증을 우회하지 않는다. Gateway의 사용자 API는 API token, admin endpoint는 admin token, Risk Adapter 직접 호출은 internal service token이 필요하다.

## 계약 정렬

FastAPI route는 플랫폼 자체 validator와 오류 매핑을 적용한다. 따라서 자동 생성 OpenAPI의 느슨한 `object` schema를 사용하지 않는다.

- `specs/schemas/*.json`은 request/response body의 단일 계약이다.
- `src/ai_model_serving/openapi_contracts.py`가 같은 schema를 생성 OpenAPI에 주입한다.
- `specs/openapi.gateway.yaml`, `specs/openapi.risk-adapter.yaml`은 배포 가능한 정적 OpenAPI 계약이다.
- `make validate`는 생성 OpenAPI와 정적 OpenAPI의 path, method, operation ID, 인증, response status, request/response schema drift를 검사한다.

따라서 route-local inline schema를 별도로 추가하지 않는다.

## 화면 구성

Gateway 문서 화면은 네 곳에 나눠 설명을 싣는다. 태그 설명의 표와 숫자는 손으로 적지 않고 `AppSettings`에서 생성한다(`src/ai_model_serving/api_descriptions.py`). 같은 값이 `/v1/models` 응답과 요청 검증에도 쓰이므로 configs를 고치면 문서가 함께 따라온다.

| 위치 | 내용 | 출처 |
|---|---|---|
| 첫 화면(`info.description`) | 빠른 시작, 인증, **요청별 디버깅**(추적 헤더·오류 본문 필드·증상별 확인 순서), readiness | 고정 문안 |
| `Models` 태그 | 모델별 backend·입력 modality·capability·파라미터 개수, `local-main` 프로필 목록과 호환성 | `settings.public_models`, `settings.main_model_profile_summaries` |
| `Chat` 태그 | 기능별 한도 — 파라미터 allowlist, 토큰 한도, 스트리밍, 도구 호출, 구조화 출력, reasoning, 진단 파라미터, 멀티모달 입력 | 기본 프로필의 `gateway_policy`, streaming/body 한도 |
| `Runtime Control` 태그 | 함대 제어 모델, GPU 예산, gate 의미, 전환 작업 stage 표 | 고정 문안 + `main_model.control.OPERATION_STAGES` |

태그 설명의 값은 **기본 프로필** 기준이다. 실행 시점 권위는 사용자에게는 `GET /v1/models`, 운영자에게는 `GET /admin/main-model`의 `active_profile.gateway_policy`다.

각 route의 summary·description은 `src/ai_model_serving/api/endpoint_spec.py`가 단일 출처이며, router는 그 값을 읽어 붙인다. status별 오류 code 설명은 `configs/error_catalog.yaml`에서 주입된다.

## 문서 화면의 역할

문서 화면은 API contract를 탐색하고 인증된 호출을 확인하는 용도다. 모델별 form UI나 별도 playground의 대체물이 아니다.

- 사용자 조정 가능 parameter는 `/v1/models`의 `request_parameters`를 기준으로 표시한다.
- runtime 하이퍼파라미터(GPU memory, model length, concurrency, quantization)는 운영자 설정이며 사용자 UI에 노출하지 않는다.
- 모델별 request form이 필요하면 model ID·capability·`request_parameters`를 `/v1/models`에서 읽어 구성한다.
- UI의 예시 sampling 값은 client preset일 뿐 Gateway 기본값을 의미하지 않는다.

권장 parameter group은 Basic generation, Advanced sampling, Streaming, Tools, Structured Outputs, Diagnostics, Token control, Multimodal input이다.

## 활성화·네트워크 정책

`FASTAPI_DOCS_ENABLED=false`일 때만 `/docs`, `/redoc`, `/openapi.json`을 비활성화한다. 기본값은 활성화다.

문서 화면을 공개해도 API 호출 권한이 생기지는 않는다. 외부 인터넷에 노출하는 환경에서는 API 인증 외에 VPN, allowlist, SSO proxy 같은 ingress 경계를 별도로 둔다.

현재 Scalar asset은 CDN 방식이다. air-gapped 또는 외부 CDN을 허용하지 않는 환경에서 self-host asset이 필요하지만, 현재 구현에는 self-host mode가 없다. 그런 환경에서는 문서 화면을 운영 경계에 맞게 제한하거나 self-host 구현을 별도 변경으로 도입한다.
