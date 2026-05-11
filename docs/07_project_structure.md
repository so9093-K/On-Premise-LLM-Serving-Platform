# 07. 프로젝트 구조

이 문서는 저장소의 주요 디렉터리와 책임을 설명한다. 설명은 한국어를 기본으로 하고, 파일명·API 경로·환경 변수·명령어는 원문을 유지한다.

| 경로 | 역할 |
|---|---|
| `src/ai_model_serving/` | Gateway, Risk Adapter, 설정, 인증, upstream client, 검증 로직 |
| `configs/` | 모델 서빙 정책, 포트, 모니터링, 이미지 태그, 운영 정책 |
| `specs/` | Gateway/Risk Adapter OpenAPI와 JSON Schema 계약 |
| `tests/` | unit/contract/integration 성격의 회귀 테스트 |
| `scripts/` | `.env` 생성, 시작/종료, readiness, smoke, 패키징, runtime validation |
| `ops/compose/` | GPU host에서 실행하는 full-stack compose reference |
| `ops/prometheus/`, `ops/grafana/` | 기본 활성화되는 운영 관측성 reference |
| `docs/` | 한국어 중심 운영·개발·품질 문서 |
| `model_cards/` | 모델별 사용 목적과 제약을 기록한 카드 |
| `harness/` | runtime validation 계획과 matrix |
| `reports/` | 현재 유지보수 결과와 runtime validation 결과 위치 |

릴리스 패키지는 `.env`, `logs/`, `run/`, `dist/`, `.runtime/`, `model_cache/`, `models/` 같은 실행 산출물과 비밀값을 제외한다.
