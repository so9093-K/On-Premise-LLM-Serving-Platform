# 로깅 정책

로그는 운영 진단에 필요한 최소 정보만 남긴다.

## 남겨도 되는 정보

- request id
- method, route, status code
- latency
- service name
- upstream logical id
- error code
- 토큰 사용량(prompt_tokens/completion_tokens/total_tokens) — 개수일 뿐 내용이
  아니라 민감정보가 아니다. `/v1/chat/completions`, `/v1/embeddings`, vLLM-backed
  prompt detector 응답의 `usage`를 `logging_policy.record_token_usage()`가 항상 옮긴다 —
  `LOG_REQUEST_RESPONSE_BODY`와 무관하게 latency와 동급으로 상시 기록된다.
  usage가 없는 local detector·입력 제한 사전 차단·실패 응답은 조용히 생략된다.

## 남기면 안 되는 정보

- prompt body
- generated output
- embedding input 원문
- Authorization header
- API key, admin key, internal token
- HF token
- secret file 내용

prompt/생성 결과 등은 끌 수 있는 옵션이 아니라 `service_logging.py`의 `SENSITIVE_KEYS`
기준으로 항상 무조건 `[REDACTED]` 처리된다(`scrub_for_log`).

## 예외: LOG_REQUEST_RESPONSE_BODY

기본 꺼짐. 켜면 아래 엔드포인트의 요청/응답 원문이 `detectors/masking.py`(PII/secret
span 마스킹, 확장 가능한 masker 목록)를 거친 뒤 `request_body`/`response_body`
필드로 `http_request_completed` 로그에 남는다 — 각 라우터가
`logging_policy.record_request_response_preview()`로 `request.state`에 세팅하면
`logging_policy.py`가 로그 레코드로 옮긴다:

- `/v1/chat/completions`(non-streaming만 — streaming은 버퍼링 없이 relay하는 기존
  설계상 이 로깅 대상에서 제외된다)
- `/v1/embeddings` — 응답은 float 벡터라 원문 대신 개수/차원/모델 요약만 남는다
  (`gateway_inference._embedding_response_summary`)
- `/v1/risk/detectors/*/assessments`, `/v1/risk/assessments` — 요청은 prompt
  원문, 응답은 판정 JSON(allow/block 등 금지 필드는 애초에 응답 계약에 없음)

위 `SENSITIVE_KEYS` 무조건 redaction과는 별개의, 명시적으로 opt-in한 경로다 —
신뢰되지 않는 트래픽이 섞이면 다시 꺼야 한다.
