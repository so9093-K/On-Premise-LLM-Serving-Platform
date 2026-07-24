# 로깅 정책

로그는 운영 진단에 필요한 최소 정보만 남긴다.

## 남겨도 되는 정보

- request id
- method, route, status code
- latency
- service name
- upstream logical id
- error code

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

기본 꺼짐. 켜면 non-streaming chat completion 요청/응답 원문이
`detectors/masking.py`(PII/secret span 마스킹, 확장 가능한 masker 목록)를 거친 뒤
`request_body`/`response_body` 필드로 `http_request_completed` 로그에 남는다
(`gateway_inference.py` -> `request.state` -> `logging_policy.py`). 위
`SENSITIVE_KEYS` 무조건 redaction과는 별개의, 명시적으로 opt-in한 경로다 —
신뢰되지 않는 트래픽이 섞이면 다시 꺼야 한다. streaming(`stream=true`) 요청/응답은
버퍼링 없이 relay하는 기존 설계상 이 로깅 대상에서 제외된다.
