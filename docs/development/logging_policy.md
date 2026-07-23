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
