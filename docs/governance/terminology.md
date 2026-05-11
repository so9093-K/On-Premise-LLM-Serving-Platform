# 용어 관리

문서는 한국어 설명을 기본으로 하되, 실행에 필요한 식별자는 원문을 유지한다.

| 용어 | 사용 기준 |
|---|---|
| Gateway | 외부 애플리케이션 진입점. 한국어로는 게이트웨이라고 설명 가능하나 코드명은 `Gateway` 유지 |
| Risk Adapter | risk detector 두 개를 호출해 signal-only 응답을 만드는 내부 서비스 |
| signal-only | `allow`, `block`, `decision`, `action` 같은 정책 결정을 포함하지 않는다는 의미 |
| readiness | `/ready`로 확인하는 dependency 준비 상태 |
| liveness | `/health`로 확인하는 프로세스 생존 상태 |
| upstream | Gateway/Risk Adapter가 호출하는 vLLM-compatible backend |
| runtime validation | 실제 실행 중인 서비스와 모델 서버를 호출하는 검증 |

영어 원문을 유지해야 하는 항목은 API path, env key, JSON/YAML field, Docker image, Python package, command, 제품명이다.

## 명령 glossary

`build`, `start`, `ready`, `smoke`, `package`, `release`, `deploy`, `up`, `down`은 서로 다른 의미로 쓴다. 특히 `deploy`는 target environment에 적용하는 행위이고, `release`는 versioned artifact를 배포 가능 상태로 만드는 행위다.
