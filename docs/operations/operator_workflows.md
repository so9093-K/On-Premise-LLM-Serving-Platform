# 운영자 Workflow 가이드

이 문서는 `make help`보다 더 짧은 **상황별 명령 선택 가이드**다. 전체 명령 목록은 `make help`, 명령 의미론은 `docs/development/build_ux.md`, 처음 프로젝트를 받았을 때의 전체 흐름은 `docs/operations/first_project_guide.md`, 빠른 초기 기동은 `docs/operations/day0_quickstart.md`를 기준으로 본다.

CLI로 같은 내용을 보고 싶으면 다음을 실행한다.

```bash
make guide
python scripts/reports/operator_guide.py --json
```

## 1. GPU 없이 app-only 개발

Gateway와 Risk Adapter process, `/health`, API docs만 확인한다.

```bash
make init-env-local
make validate
make test
make start
make ready-local
make status
make stop
```

주의:
- app-only 모드에서는 `make ready-full` 대신 `make ready-local`을 사용한다.
- `make init-env-compose`로 만든 `.env`는 compose 내부 hostname을 사용하므로 app-only health 확인에 맞지 않는다.

## 2. GPU/vLLM full-stack 검증

대상 GPU 서버에서 compose, enabled vLLM runtime 3개, Prometheus, Grafana까지 확인한다.

```bash
HF_TOKEN=hf_xxx make first-run
source .venv/bin/activate
make compose-up
make ready-full
make runtime-validate
make operator-reports
make compose-down
```

주의:
- 이미 `.env`에 `HF_TOKEN`이 있으면 `HF_TOKEN=...` 주입은 생략 가능하다.
- 앱 코드만 반복 수정할 때는 `SKIP_RISK_VLLM_IMAGE_BUILD=auto make rebuild-full`을 사용한다.

## 3. 운영 산출물 갱신

서비스를 새로 띄우지 않고 registry 기반 runtime/storage/monitoring/status/evidence 산출물을 갱신한다.

```bash
make operator-reports
```

이는 아래 명령을 순서대로 실행하는 단축 target이다.

```bash
make runtime-targets
make monitoring-projection
make operator-status
make live-evidence
```

생성 산출물:

```text
reports/runtime/runtime_targets.json
reports/runtime/monitoring_projection.json
reports/runtime/operator_status_bundle.json
reports/runtime/live_evidence_bundle.json
```

`make live-evidence`는 가장 최신 live runtime validation report를 우선 사용한다. 대상 GPU 서버에서 새 live evidence를 만들려면 먼저 `make runtime-validate`를 실행한다.

## 4. 릴리스 전 정적 gate

서비스 기동 없이 계약, 설정, projection, evidence bundle을 검증한다.

```bash
make validate
make test
make package
```

- `make validate`: 정적 계약, config-only runtime harness, compose validation, operator projection을 확인한다.
- `make test`: deterministic unit·contract test suite를 실행한다.
- `make package`: release ZIP을 만든다.

이 흐름은 대상 GPU 서버 live runtime gate를 대체하지 않는다.

## 5. 정리/초기화

삭제 전에는 항상 plan을 먼저 본다.

```bash
make remove-plan
```

범위별 명령:

| 목적 | 명령 |
|---|---|
| 삭제 대상 미리 보기 | `make remove-plan` |
| build/dist/cache/run 산출물 삭제 | `make clean` |
| 일반 산출물 + logs 삭제 | `make clean-all` |
| 모델 캐시까지 삭제 | `PURGE_MODEL_CACHE=1 make clean-all` |
| runtime secret까지 재생성하고 싶음 | `PURGE_RUNTIME_SECRETS=1 make clean-all` |
| Docker 이미지 포함 통합 제거/초기화 | `make reset` |
| 이미지 + venv + 시크릿 + 모델까지 전체 초기화 | `PURGE_MODEL_CACHE=1 PURGE_RUNTIME_SECRETS=1 PURGE_VENV=1 make reset` |

`make clean`과 `make clean-all`은 Docker 이미지를 삭제하지 않는다. 이미지까지 정리하려면 `make reset`을 사용한다.


## 인증 제어 플레인 점검

`make auth-status`로 public/admin/internal auth의 실제 상태를 확인하고, `make auth-doctor`로 위험하거나 일관되지 않은 flag 조합을 탐지한다. 변경 전에는 `make auth-plan MODE=strict`로 profile 변경을 미리 보고, 적용은 `make auth-apply MODE=strict`로 managed auth flag만 갱신한다. 이 점검은 secret 값을 출력하지 않으며 API 기능을 바꾸지 않는다.
