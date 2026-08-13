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
