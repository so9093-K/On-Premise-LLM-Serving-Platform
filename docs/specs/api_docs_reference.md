# API Docs Reference

## 1. 기본 정책

이 정책은 “한국어 설명 + 영어 식별자 유지”를 기준으로 한다. API 문서 화면은 local, compose, staging, production-like 검증 환경에서 기본 활성화한다.

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
- `local-main`, `local-embed`, `local-embed-ko`, `risk-prompt` logical model id
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

**FastAPI docs(`/docs`)는 contract reference이며 model-aware playground가 아니다.** 모델별 form UI는 `/v1/models` 기반으로 구성해야 한다.

- Chat UI는 `local-main.request_parameters`를 읽어 `temperature`, `max_tokens`, `top_p`, `top_k`, `min_p`, penalty, `seed`, `n`, tool 관련 입력, `reasoning` opt-in을 구성한다.
- Chat 예시는 최소 요청, deterministic smoke, 일반 sampling, streaming, json_object, json_schema, tool calling, reasoning/thinking, logprobs, bounded vision을 분리한다. 예시의 sampling 값은 client preset이며 Gateway가 자동 주입하는 기본값이 아니다.
- Embedding UI는 `local-embed`와 `local-embed-ko`의 `request_parameters`를 모델별로 읽어 `dimensions`, `encoding_format`, `truncate_prompt_tokens`를 노출한다. `local-embed-ko`의 `dimensions`는 1024만 허용한다. `encoding_format: base64`는 현재 지원하지 않으므로 example에 포함하지 않는다.
- Risk UI는 `risk-prompt`의 `request_parameters`가 비어 있음을 보고 prompt 입력만 노출한다. `fixed_parameters`는 detector adapter 내부값이므로 사용자 form으로 노출하지 않는다.

고급 parameter의 상세 정책(`$ref` subset, `logit_bias` tokenizer 주의사항, `capability_gate` 동작 등)은 top-level description이 아닌 operation description 또는 `docs/specs/api.md`, `docs/operations/model_parameter_discovery.md`로 분리한다.

## 3.3 parameter grouping (UI 권장)

모델별 parameter를 표시할 때 다음 grouping을 권장한다.

| 그룹 | parameter |
|---|---|
| Basic generation | `max_tokens`, `temperature`, `top_p` |
| Advanced sampling | `top_k`, `min_p`, `presence_penalty`, `frequency_penalty`, `repetition_penalty`, `seed`, `n` |
| Streaming | `stream`, `stream_options` |
| Tools | `tools`, `tool_choice`, `parallel_tool_calls` |
| Structured Outputs | `response_format` |
| Diagnostics | `logprobs`, `top_logprobs` |
| Advanced token control | `logit_bias` |
| Vision | `image_url` content part |

`/v1/models`에 `request_parameter_groups` 같은 새 field를 추가하는 것은 별도 PR로 진행한다.

## 3.4 /playground 설계 (TODO)

이번 PR에서 `/playground` 실제 구현은 하지 않는다. 후속 작업의 방향만 기록한다.

- `/playground`는 `/v1/models`를 읽어 model-aware form을 구성한다.
- 포함할 요소: model selector, capability badge, parameter group, request JSON preview, curl/code copy, streaming viewer, json_schema editor, logprobs/logit_bias advanced section.

## 4. docs asset CDN/self-host 운영 정책

현재 Scalar UI는 CDN에서 `@scalar/api-reference`를 로드한다.

| 환경 | 권장 정책 |
|---|---|
| local / dev | CDN mode 허용 |
| staging / prod / private network | pinned version 또는 self-host asset 권장 |
| air-gapped / offline / local-only network | self-host asset 필요 |

현재 구현은 CDN 기반이다. self-host asset mode는 후속 작업으로 둔다. 설정 설계안:

```yaml
documentation:
  ui: scalar
  asset_mode: cdn
  scalar_asset_url: https://cdn.jsdelivr.net/npm/@scalar/api-reference
```

구현은 이번 PR 범위 밖이다.

## 5. 비활성화

문서 화면을 끄고 싶을 때만 명시한다.

```bash
FASTAPI_DOCS_ENABLED=false
```

이 값은 기본 차단이 아니라 명시적 운영 결정으로 취급한다.

## 6. 보안 경계

문서 화면 자체는 API 호출 권한을 주지 않는다. `/v1/*`, `/ready`, `/metrics`의 실제 접근 권한은 Bearer token, admin token, 네트워크/ingress 설정으로 관리한다. public internet에 직접 노출할 경우 VPN, allowlist, SSO proxy 같은 별도 경계를 적용한다.
