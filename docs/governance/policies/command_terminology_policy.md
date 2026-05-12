# 명령어 용어 정책

이 정책은 명령어 이름을 일관되게 유지하기 위한 기준이다. 정책 이름은 기존 contract 호환을 위해 영어 원문을 유지하지만, 설명은 한국어를 기본으로 한다.

## 원칙

- `make start`는 로컬 app-only Gateway/Risk Adapter를 시작한다.
- `make up`은 `make start`의 alias다.
- `make compose-up`은 `ops/compose/full-stack.private-network.yaml` 전체 stack을 시작한다.
- `make stop`과 `make down`은 로컬 app-only 프로세스를 멈춘다.
- `make compose-down`은 full-stack compose를 내린다.
- `make ready`는 live service readiness와 smoke 성격의 점검을 실행한다.
- `make clean`은 생성 산출물을 지우되 logs는 보존한다.
- `make clean-all`은 logs까지 지우고, model cache는 `PURGE_MODEL_CACHE=1`, runtime secret은 `PURGE_RUNTIME_SECRETS=1`일 때만 지운다.

사용자 문서에는 alias보다 실제 범위가 분명한 명령을 먼저 적는다. 예를 들어 full-stack 종료는 `make down`이 아니라 `make compose-down`으로 안내한다.

## 호환성 문구 기준

`make up`은 Docker Compose 친화 alias로 유지한다. `make down`은 local stop alias이며, full-stack compose stack 종료에는 `make compose-down`을 사용한다.

## 필수 문서 문구

- build, start, readiness, deploy, release는 서로 다른 동작이다.
- `make build`는 artifact/image를 생성하고 검증한다.
- `make start`는 local service 또는 compose stack을 시작한다.
- `make ready`는 live stack readiness를 증명한다.
- `make up`은 Docker Compose 친화 alias로 유지한다.
