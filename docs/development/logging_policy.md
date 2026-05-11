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

`LOG_PROMPT_BODY=false`, `LOG_MODEL_RAW_OUTPUT=false`가 기본이다. 운영 환경에서 원문 로깅을 켜지 않는다.
