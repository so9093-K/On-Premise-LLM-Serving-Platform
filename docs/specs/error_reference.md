<!-- GENERATED FILE. DO NOT EDIT MANUALLY. -->
<!-- Source: src/ai_model_serving/errors.py (ERROR_STATUS) + configs/error_catalog.yaml -->
<!-- Command: make render-runtime-assets -->

# 에러 코드 레퍼런스

Gateway·Risk Adapter의 모든 에러는 동일한 봉투를 따른다:

```json
{ "error": { "code": "...", "message": "...", "param": "...", "retryable": false, "request_id": "req_..." } }
```

- `code` — 안정적 기계 판독 식별자(아래 표). HTTP status와 항상 일치한다.
- `param` — 오류를 일으킨 요청 필드. 예: 잘못된 출력 스펙은 `response_format.json_schema`, 잘못된 입력 데이터 포맷은 `input_audio.format`. 필드 범위 검증 오류에만 존재한다(OpenAI 호환).
- `retryable` — 재시도 권장 여부. 응답값이 권위이며 아래 표는 일반값이다.
- `request_id` — 지원 문의 시 인용한다.

| code | HTTP | retryable | 의미 | 권장 조치 |
|---|---:|:---:|---|---|
| `UNAUTHORIZED` | 401 | ✗ | API 키 또는 인증 정보가 없거나 유효하지 않다. | 유효한 Authorization 헤더(API 키)를 포함해 다시 호출한다. |
| `FORBIDDEN` | 403 | ✗ | 인증은 되었으나 해당 작업을 수행할 권한이 없다. | 필요한 권한/프로파일로 호출하거나 운영자에게 문의한다. |
| `NOT_FOUND` | 404 | ✗ | 요청한 리소스(엔드포인트, operation 등)가 존재하지 않는다. | 요청 경로와 식별자를 확인한다. |
| `DETECTOR_DISABLED` | 410 | ✗ | 요청한 risk detector 가 현재 설정에서 비활성이다. | 운영자에게 활성화를 요청하거나 다른 detector 를 사용한다. |
| `DETECTOR_RETIRED` | 410 | ✗ | 요청한 risk detector 가 폐기되어 더 이상 제공되지 않는다. | 현행 detector/엔드포인트로 전환한다. |
| `REQUEST_TOO_LARGE` | 413 | ✗ | 요청 body 가 허용 한도를 초과했다. | payload(이미지·오디오·비디오·문서) 크기를 한도 이하로 줄인다. |
| `MODEL_CAPABILITY_MISMATCH` | 422 | ✗ | 요청이 활성 모델이 지원하지 않는 기능을 요구한다(예 - 비활성 모달리티). | 활성 프로파일이 지원하는 입력/기능으로 요청을 맞춘다. |
| `VALIDATION_ERROR` | 422 | ✗ | 요청이 Gateway 입력 계약 검증을 통과하지 못했다. | error.param 이 가리키는 필드를 message 지시대로 수정한다. |
| `RATE_LIMITED` | 429 | ✓ | upstream rate limit 또는 로컬 admission 한도에 도달했다. | Retry-After 를 존중해 백오프 후 재시도한다. |
| `INTERNAL_ERROR` | 500 | ✗ | Gateway 내부 처리 중 예기치 못한 오류가 발생했다. | request_id 와 함께 운영자에게 문의한다. |
| `PARSE_ERROR` | 502 | ✗ | upstream 응답을 파싱할 수 없었다(유효하지 않은 형식). | 반복되면 런타임 상태·로그를 확인한다. |
| `UPSTREAM_ERROR` | 502 | ✓ | upstream 런타임이 오류를 반환했거나 통신에 실패했다. | 잠시 후 재시도한다. 반복되면 런타임 로그를 확인한다. |
| `UPSTREAM_SCHEMA_ERROR` | 502 | ✗ | structured output 생성이 요청한 json_schema 를 만족하지 못했다. | response_format.json_schema 를 단순화하거나 max_tokens 를 늘린다. |
| `CIRCUIT_OPEN` | 503 | ✓ | 연속 실패로 upstream 서킷이 열려 일시적으로 차단 중이다. | 잠시 후 재시도한다. |
| `MAIN_MODEL_CONTROL_UNAVAILABLE` | 503 | ✓ | 메인 모델 control plane(admin sidecar)을 사용할 수 없다. | 잠시 후 재시도한다. 반복되면 sidecar 상태를 확인한다. |
| `MAIN_MODEL_SWITCH_IN_PROGRESS` | 503 | ✓ | 메인 모델 프로파일 전환이 진행 중이다. | 전환 완료(operation 상태) 후 재시도한다. |
| `MODEL_PARKED` | 503 | ✓ | 모델이 park(대기) 상태라 즉시 서빙할 수 없다. | 모델을 활성화하거나 잠시 후 재시도한다. |
| `MODEL_UNAVAILABLE` | 503 | ✓ | 대상 모델 런타임을 현재 사용할 수 없다(미기동·축출 등). | 잠시 후 재시도하거나 런타임 상태(/admin/runtimes)를 확인한다. |
| `QUEUE_TIMEOUT` | 503 | ✓ | 동시 처리 한도로 대기열에서 시간이 초과됐다. | 잠시 후 재시도하고 동시 요청 수를 줄인다. |
| `RUNTIME_NOT_READY` | 503 | ✓ | 런타임이 아직 기동/준비 중이다. | 준비 완료 후 재시도한다(/health 와 런타임 상태 확인). |
| `STREAM_LIMIT_EXCEEDED` | 504 | ✗ | 스트리밍 응답이 chunk/byte 한도를 초과했다. | 출력 길이를 줄이거나 비스트리밍으로 재시도한다. |
| `UPSTREAM_TIMEOUT` | 504 | ✓ | upstream 런타임 응답이 제한 시간을 초과했다. | 잠시 후 재시도하거나 요청 크기·복잡도를 줄인다. |
