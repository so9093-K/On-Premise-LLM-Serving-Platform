# ADR 0004. 외부 진입 포트 9400 정책

## 결정

외부 애플리케이션 진입점은 Gateway `9400`으로 둔다. Risk Adapter는 내부 서비스 포트 `9405`를 사용하며, 운영자가 FastAPI Docs를 확인할 수 있도록 compose 예시에서는 host port를 게시한다.

## 이유

- 사용자는 하나의 Gateway endpoint를 기준으로 chat, embedding, risk signal을 호출할 수 있다.
- Risk Adapter는 독립적으로 배포·관찰할 수 있지만 외부 정책 판단 API가 아니다.
- `/docs`, `/redoc`, `/openapi.json`은 초기 운영과 디버깅을 위해 기본 활성화한다.

## 영향

보안이 필요한 환경에서는 ingress, firewall, private network 정책으로 포트 노출 범위를 조정한다. 애플리케이션 기본값은 운영자가 바로 확인할 수 있는 사용성을 우선한다.
