# 모델 런타임 제어

Gateway는 모델 서버를 직접 생성하지 않는다. vLLM process/container는 compose 또는 운영 플랫폼이 관리하고, Gateway는 HTTP upstream으로 호출한다.

## 제어 지점

- endpoint URL은 enabled runtime 기준 `MAIN_LLM_BASE_URL`, `EMBEDDING_BASE_URL`, `RISK_PROMPT_BASE_URL`로 관리한다.
- served model name은 enabled runtime 기준 `MAIN_LLM_MODEL`, `EMBEDDING_MODEL`, `RISK_PROMPT_MODEL`로 관리한다.
- timeout은 `*_TIMEOUT_SECONDS`, `REQUEST_TIMEOUT_SECONDS`, `RISK_ADAPTER_TIMEOUT_SECONDS`로 관리한다.
- admission control은 `*_MAX_CONCURRENCY`, `*_QUEUE_TIMEOUT_SECONDS`로 관리한다.
- circuit breaker는 `*_CIRCUIT_BREAKER_FAILURE_THRESHOLD`, `*_CIRCUIT_BREAKER_RESET_SECONDS`로 관리한다.

## readiness

Gateway `/ready`는 main LLM, embedding, Risk Adapter를 확인한다. Risk Adapter `/ready`가 admin auth를 요구하면 Gateway는 내부 admin bearer token을 전달한다. Gateway는 Risk Adapter의 HTTP 성공만으로 ready 처리하지 않고, 응답 body의 `status`가 `ready`일 때만 Risk Adapter dependency를 ready로 기록한다.

모델이 실제로 로딩 중이면 `/ready`는 HTTP 503을 유지하되 body에 `phase: waiting_for_dependencies`, `not_ready_dependencies`, dependency별 `endpoint`와 `message`를 포함한다. 따라서 UI나 운영 스크립트는 HTTP status로 traffic gate를 막고, body로 어떤 vLLM runtime을 기다리는지 표시한다.

## 모델 lifecycle과 읽기 전용 제어 플레인

운영자는 모델 상태를 YAML 파일을 직접 뒤지지 않고 먼저 `modelctl` projection으로 확인한다.

```bash
python scripts/models/modelctl.py list
python scripts/models/modelctl.py status
python scripts/models/modelctl.py validate
python scripts/models/modelctl.py diff
python scripts/models/modelctl.py propose-add --id new-main --role main_llm --upstream-model-id org/model --port 9499 --endpoint /v1/new-main
python scripts/models/modelctl.py propose-remove local-main
python scripts/models/modelctl.py propose-add --id new-main --role main_llm --upstream-model-id org/model --port 9499 --endpoint /v1/new-main --write-plan --write-patch
python scripts/models/modelctl.py propose-remove local-main --write-plan --write-patch
make model-status
make model-validate
make model-propose-add ID=new-main PORT=9499 ENDPOINT=/v1/new-main UPSTREAM=org/model ROLE=main_llm
make model-propose-add ID=new-main PORT=9499 ENDPOINT=/v1/new-main UPSTREAM=org/model ROLE=main_llm WRITE_PLAN=1 WRITE_PATCH=1
make model-propose-remove ID=local-main WRITE_PLAN=1 WRITE_PATCH=1
```

- Add a model / 모델 추가: 먼저 `propose-add`로 id/port/endpoint/runtime service 충돌, GPU budget 경고, 영향 파일 목록을 확인한다. 실제 반영은 `configs/model_catalog.yaml`, `configs/model_serving.yaml`, OpenAPI/schema, tests를 함께 수정하는 리뷰 절차로 진행한다.
- Remove a model / 모델 제거: 먼저 `propose-remove`로 단계적 deprecation plan을 확인한다. 곧바로 삭제하지 않고 `lifecycle.state=deprecated|disabled`와 `exposure=hidden|internal`로 축소한 뒤, public listing, route, config, model card, tests를 함께 제거한다.
- Model independence: 한 모델의 timeout/concurrency 조정이 다른 모델 id나 API contract를 바꾸면 안 된다.
- Input and output contracts: request/response schema는 Gateway에서 검증한다.
- lifecycle state는 `experimental`, `active`, `deprecated`, `disabled`, `retired`, `removed`를 사용한다.
- exposure 값은 `public`, `internal`, `hidden`을 사용한다.

쓰기형 add/remove 명령은 아직 source 파일을 직접 수정하지 않는다. 현재 단계에서는 `modelctl`의 `list/status/validate/diff`가 registry/projection drift를 읽고 검증하고, `propose-add/propose-remove`는 기본적으로 파일 쓰기 없는 plan-only control plane으로 동작한다. `--write-plan`을 주면 `reports/model_changes/*.plan.json`과 `*.plan.md`를 남기고, `--write-patch`를 주면 사람이 리뷰할 수 있는 `*.patch-scaffold.md`까지 생성한다. 이 artifact는 review/checklist 용도이며 `configs/`, `contracts/`, `specs/` 원본 파일은 수정하지 않는다.


## 로컬 readiness와 full readiness 구분

`make start`는 Gateway와 Risk Adapter만 시작한다. 모델 runtime은 시작하지 않는다. 따라서 app-only health gate에서는 `make ready-local`을 사용하고, 실제 vLLM dependency까지 포함한 운영 gate에서는 `make ready-full`을 사용한다. `make ready-local`은 app process가 내려가 있으면 실패하지만 vLLM `/ready` 상태는 요구하지 않는다.
