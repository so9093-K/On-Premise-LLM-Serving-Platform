# 문서 관리 정책

## 1. 기본 언어

이 프로젝트의 기본 설명 언어는 한국어다. 한국어 사용자를 위해 별도 `*_KO.md` 문서를 추가하는 방식이 아니라, README와 주요 운영 문서 자체를 한국어 중심으로 작성한다.

## 2. 영어 원문 유지 대상

다음은 번역하지 않는다.

- API path: `/docs`, `/v1/chat/completions`
- 환경 변수: `API_KEYS`, `ADMIN_API_KEY_REQUIRED`
- JSON/YAML field: `model`, `messages`, `risk_code`
- 명령어: `make compose-up`, `docker compose`
- 제품명/기술명: `FastAPI`, `Prometheus`, `Grafana`, `vLLM`

## 3. 중복 문서 금지

동일한 내용을 영어 문서와 한국어 문서로 나눠 병렬 유지하지 않는다. 공개/외부 배포가 필요하면 한국어 본문 아래에 짧은 English summary만 둔다.

## 4. 우선순위

| 정보 | 기준 위치 |
|---|---|
| 시작/실행 | `README.md` |
| 전체 구조 | `docs/00_executive_summary.md`, `docs/06_architecture.md` |
| 운영 실행 | `docs/operations/full_stack_runtime.md`, `docs/operations/first_project_guide.md` |
| API | `docs/specs/api.md`, `specs/openapi/*.yaml` |
| 설정 | `docs/specs/configuration.md`, `configs/*.yaml` |
| 모니터링 | `docs/operations/monitoring_ux.md`, `configs/monitoring.yaml` |
| 빌드/패키징 | `docs/development/build_ux.md` |
| 릴리스 | `docs/release/release_checklist.md`, `docs/release/versioning_policy.md` |
