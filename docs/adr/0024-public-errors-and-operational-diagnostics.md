# ADR-0024: 공개 오류 계약과 운영 진단의 경계

## Status

Accepted

## Context

하나의 HTTP 상태나 `UPSTREAM_SCHEMA_ERROR`로 응답 구조 불일치와 생성 JSON 실패를
묶으면 클라이언트가 무엇을 고칠지 알 수 없다. 반대로 내부 원인마다 공개 코드를
만들면 API가 runtime 구현에 종속된다. 응답 헤더를 로그 전달 수단으로 사용하면
미들웨어 바깥의 500과 HTTP 200 이후의 SSE 오류도 일관되게 기록하기 어렵다.

## Decision

- 공개 `error.code`는 호출자의 복구 행동이 달라지는 경우에 구분한다. 정해진 코드
  개수에 맞추지 않는다. `param`은 요청 수정 위치, `details`는 구조화된 복구 정보다.
- `errors.py:ERROR_DEFINITIONS`가 status·retryable을 소유한다. YAML catalog는
  의미·조치만 설명한다. 오류 생성 함수의 호출자는 status·retryable을 다시 입력할 수 없다.
  기존 schema/code 집합 검증을 유지하고 중복 검증기는 만들지 않는다.
- `api/endpoint_spec.py`가 endpoint별 공개 오류 code를 소유한다. runtime OpenAPI와
  `specs/openapi.*.yaml`은 이 목록과 `ERROR_DEFINITIONS`에서 status별 schema를 생성한다.
  모든 POST에 upstream 오류를 일괄 주입하는 규칙은 사용하지 않는다.
- 응답 구조·model ID 불일치는 `UPSTREAM_RESPONSE_INVALID`, 생성 JSON의 파싱·schema
  불일치는 `STRUCTURED_OUTPUT_INVALID`다. 요청 자체의 잘못된 `$ref`는 추론 전에 422다.
- 내부 `diagnostics`와 `diagnostic_code`는 공개 envelope에 넣지 않는다. 요청 로그는
  기존 마스킹을 거친 원인 allowlist만 기록한다. 별도 원인 코드가 없으면 공개 코드를
  운영 분류로 사용하며, 기존 `upstream_errors_total{code=...}`에도 같은 분류를 사용한다.
- 응답 헤더는 상관 ID·공개 오류 코드·재시도 대기 등 클라이언트 용도만 담당한다.
  ASGI 요청 state를 공유해 응답 본문/스트림 종료 후 요청 이벤트를 한 번 기록한다.
  외부 500 처리 전에 발생한 예외도 같은 request ID로 로그와 응답을 연결한다.
- `STREAM_LIMIT_EXCEEDED`는 SSE 종료 이벤트이며 가짜 HTTP 504를 부여하지 않는다.
  대시보드는 HTTP 오류와 HTTP 200 이후 SSE 오류를 함께 조회한다.
- `retryable=true`는 일시적 실패를 뜻하며 부분 결과·중복 실행의 안전성을 보장하지 않는다.

## Consequences

- 기존 `UPSTREAM_SCHEMA_ERROR`, 공개 `PARSE_ERROR`, `DETECTOR_DISABLED`의 410,
  `error.debug` 또는 `X-Error-Message`를 소비하던 클라이언트는 변경이 필요하다.
  정확한 이전→이후 대응은 CHANGELOG에 기록한다. Risk의 도메인 신호 `PARSE_ERROR`는 유지한다.
- 로그 `latency_ms`는 스트림 전체 처리 시간을 포함한다. 기존 헤더 시점 HTTP latency
  metric과 같은 값으로 해석하지 않는다. 열린 스트림은 종료 전 완료 이벤트가 없다.
- 마스킹·인증·노출 정책, Risk 판단과 runtime tuple은 변경하지 않는다.
- 검증은 로컬 application·contract 경계에서 수행한다. GPU backend 동작을 바꾸지 않는
  오류 변환 때문에 기존 Ubuntu/NVIDIA qualification을 반복하지 않는다.
- 정적 OpenAPI는 수동으로 고치는 별도 기준이 아니라 `make render-runtime-assets`의
  생성 산출물이며, snapshot 검사는 status뿐 아니라 endpoint별 오류 code enum도 비교한다.
