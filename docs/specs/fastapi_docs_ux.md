# FastAPI Docs UX 기준

## 1. 기본 정책

`0.1.0-rc.1` 기준 이 정책은 “한국어 설명 + 영어 식별자 유지”를 기준으로 한다. API 문서 화면은 local, compose, staging, production-like 검증 환경에서 기본 활성화한다.

| 항목 | 기본값 |
|---|---|
| **Scalar UI** | `/docs` |
| ReDoc | `/redoc` |
| OpenAPI JSON | `/openapi.json` |
| 기본 상태 | 활성화 |

`/docs`는 FastAPI 기본 Swagger UI 대신 **Scalar**를 사용한다. Scalar는 CDN 방식(`@scalar/api-reference`)으로 주입하며 별도 pip 패키지 없이 동작한다. ReDoc은 읽기 전용 문서로 `/redoc`에서 유지한다.

운영 초기에는 문서 화면을 막는 것보다 실제 호출 권한과 네트워크 경계를 명확히 하는 편이 낫다.

## 2. Gateway UX

| URL | 용도 |
|---|---|
| `http://<host>:9400/docs` | **Scalar UI** — Bearer token 입력 후 테스트 호출 |
| `http://<host>:9400/redoc` | ReDoc — 읽기 전용 API 문서 확인 |
| `http://<host>:9400/openapi.json` | client 생성, diff, contract validation |

문서 화면은 다음 정보를 첫 화면에서 이해할 수 있어야 한다.

- Gateway API token과 admin token의 용도 차이
- app-only `/health`와 full-stack `/ready`의 차이
- 모델 로딩 중 `/ready`가 HTTP 503을 유지하되 `phase`, `not_ready_dependencies`, dependency별 `message`를 제공한다는 점
- `local-main`, `local-embed`, `risk-prompt`, `risk-siren` logical model id
- `/v1/models`에서 모델별 `capabilities`, `request_parameters`, `fixed_parameters`를 확인할 수 있다는 점
- Risk API는 signal-only이며 최종 policy decision을 반환하지 않는다는 점

태그는 영어(`Operations`, `Monitoring`, `Models`, `Chat`, `Embeddings`, `Risk`), endpoint 설명은 한국어로 작성한다. ReDoc은 tag 설명과 endpoint description을 통해 “언제 사용하는 endpoint인지”를 먼저 보여준다.

## 3. Risk Adapter UX

Risk Adapter는 내부 서비스지만 compose/staging 검증 중에는 직접 확인할 필요가 있다.

| URL | 용도 |
|---|---|
| `http://<host>:9405/docs` | detector endpoint 직접 확인 |
| `http://<host>:9405/redoc` | signal-only contract 확인 |
| `http://<host>:9405/openapi.json` | 내부 API contract 확인 |

실제 호출은 internal service token을 요구한다.

Risk Adapter 문서는 내부 API이지만 다음을 분명히 설명해야 한다.

- Gateway 또는 내부 호출자만 `/v1/risk/*`를 호출한다.
- `/ready`는 detector vLLM runtime 준비 상태를 보여준다.
- detector failure는 system signal로 표현되며 최종 allow/block 판단으로 바꾸지 않는다.


## 3.1 Generated OpenAPI 계약 정렬

FastAPI route handler는 `dict[str, Any]`를 받아 플랫폼의 자체 contract validator와 error mapping을 적용한다. 따라서 generated OpenAPI는 FastAPI 기본 loose object schema에 의존하지 않고 checked-in JSON schema에서 patch해야 한다.

`src/ai_model_serving/openapi_contracts.py`는 `specs/schemas/*.json`의 schema를 Gateway와 Risk Adapter request/response body의 generated OpenAPI에 주입한다. Governance validation과 unit test는 generated OpenAPI를 checked-in schema 문서와 비교해 `/docs`와 runtime validation이 drift되지 않게 한다.

Checked-in JSON schema가 API 문서의 source of truth다. Route-local inline schema는 다시 도입하지 않는다.


## 3.2 사용자 조정 가능 파라미터 표시

API 문서는 “사용자가 조정할 수 있는 parameter”와 “운영자가 config로 고정하는 runtime 하이퍼파라미터”를 분리해서 보여줘야 한다. `/v1/models` 응답의 `request_parameters`는 사용자-facing 조정 가능 parameter의 source of truth다.

- Chat UI는 `local-main.request_parameters`를 읽어 `temperature`, `max_tokens`, `top_p`, `top_k`, `min_p`, penalty, `seed`, `n`, tool 관련 입력을 구성한다.
- Embedding UI는 `local-embed.request_parameters`를 읽어 `dimensions`, `encoding_format`, `truncate_prompt_tokens`만 노출한다.
- Risk UI는 `risk-prompt`/`risk-siren`의 `request_parameters`가 비어 있음을 보고 prompt 입력만 노출한다. `fixed_parameters`는 detector adapter 내부값이므로 사용자 form으로 노출하지 않는다.

## 4. 비활성화

문서 화면을 끄고 싶을 때만 명시한다.

```bash
FASTAPI_DOCS_ENABLED=false
```

이 값은 기본 차단이 아니라 명시적 운영 결정으로 취급한다.

## 5. 보안 경계

문서 화면 자체는 API 호출 권한을 주지 않는다. `/v1/*`, `/ready`, `/metrics`의 실제 접근 권한은 Bearer token, admin token, 네트워크/ingress 설정으로 관리한다. public internet에 직접 노출할 경우 VPN, allowlist, SSO proxy 같은 별도 경계를 적용한다.
