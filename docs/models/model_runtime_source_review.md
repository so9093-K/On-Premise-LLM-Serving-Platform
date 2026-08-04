# 모델 런타임 출처 검토

이 프로젝트는 `src/` 안에 fake runtime을 포함하지 않는다. Gateway와 Risk Adapter는 설정된 vLLM-compatible upstream을 호출한다.

## 출처 구분

| 구분 | 위치 | 설명 |
|---|---|---|
| 모델 사실 | `model_cards/`, upstream model card | 모델명, 라이선스, 입력 형식, 기본 능력 |
| 프로젝트 운영 정책 | `configs/model_serving.yaml` | concurrency, timeout, queue, output cap |
| API 계약 | `specs/`, `contracts/` | Gateway가 허용하는 request/response 형태 |
| 테스트 double | `tests/` | unit test 전용 fake client |

운영 문서에서는 upstream 모델 card의 사실과 이 프로젝트의 자원 제한을 섞어 쓰지 않는다.

## Contract phrase 기준

`source_facts`는 upstream 모델 카드나 공개 예시의 사실을 기록한다. `project_runtime_policy`는 이 프로젝트가 적용하는 timeout, concurrency, token cap을 기록한다. risk detector의 runtime `max_output_tokens=1`은 배포 설정 정합성 검증으로 보호한다.
