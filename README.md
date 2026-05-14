# AI 모델 서빙 플랫폼

> vLLM 기반 LLM·Embedding·Risk Signal 서비스를 Gateway 하나로 통합하고, API 계약·모델 구성·운영 검증·모니터링을 일관되게 제공하는 모델 서빙 플랫폼이다.

| 패키지 버전 | `0.0.1` |
|---|---|
| 권장 runtime | Python 3.12.13 |
| GPU | NVIDIA RTX 6000 Ada Generation 48GB |

---

## 무엇을 할지 먼저 고른다

| 상황 | 핵심 명령 |
|---|---|
| 처음 받았다 / 전체 흐름을 알고 싶다 | `make help` → `make guide` |
| GPU 없이 코드·API만 확인 | [§ App-only 경로](#app-only-경로-gpu-없음) |
| GPU 서버에서 full-stack 실행 | [§ Full-stack 경로](#full-stack-경로-gpu-필요) |
| 빌드·패키징 흐름 | `make build-pipeline` → `make package` |
| 장애가 났다 | `make doctor` → `make compose-diagnostics` |
| 상황별 명령을 고르고 싶다 | `make guide` |

> `make ready`는 full-stack compose 전용이다. app-only에서는 반드시 `make ready-local`을 사용한다.

---

## 프로젝트 개요

![AI 모델 서빙 플랫폼 시스템 구성도](assets/ai_model_serving_system_architecture.jpg)

이 프로젝트는 애플리케이션이 개별 모델 runtime에 직접 붙지 않고 Gateway `9400` 하나를 통해 다음 기능을 사용하도록 표준화한다.

| 기능 | 논리 모델 | API |
|---|---|---|
| 텍스트 생성 | `local-main` | `/v1/chat/completions` |
| 임베딩 | `local-embed` | `/v1/embeddings` |
| 위험 신호 분석 | `risk-prompt` | `/v1/risk/*` |

Risk Adapter는 `allow`, `block`, `decision`, `action` 같은 최종 정책 결정을 하지 않는다. detector 결과를 **signal-only response**로 정규화하고, 최종 제품 정책은 Gateway 밖 별도 product policy layer가 담당한다.

서비스 코드는 `src/ai_model_serving/`에 있다. 운영 코드에는 fake model response를 넣지 않는다. 실제 vLLM/GPU/Prometheus/Grafana 검증 결과는 target host에서 `reports/runtime/`에 runtime validation report로 생성한다.

---

## 시스템 요구사항

| 항목 | 요구사항 |
|---|---|
| GPU | NVIDIA RTX 6000 Ada Generation 48GB 또는 동급 48GiB VRAM 단일 GPU |
| CPU | 16 vCPU 이상 권장 |
| RAM | 96GiB 이상 권장, 최소 64GiB |
| Disk | NVMe 500GB 이상 권장 |
| Python | App/control-plane `>=3.12,<3.15`; 권장 production runtime CPython 3.12.13 |
| Runtime | Docker, NVIDIA Container Toolkit, vLLM |

---

## App-only 경로 (GPU 없음)

Gateway와 Risk Adapter만 로컬 process로 실행한다. vLLM 모델 서버를 시작하지 않으므로 `/health`는 성공해도 `/ready`는 `not_ready`가 될 수 있다.

> `make init-env-local`을 사용한다. `make init-env-compose`로 생성한 `.env`에는 `RISK_ADAPTER_BASE_URL=http://risk-adapter:9405` 같은 compose 내부 hostname이 들어가므로 app-only에서 `make ready`가 실패한다. app-only readiness 확인은 `make ready-local`을 사용한다.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python3.12 -m pip install --upgrade pip
python3.12 -m pip install --requirement requirements.lock
python3.12 -m pip install --no-deps -e ".[contract]"

make init-env-local
make validate
make test
make start
make ready-local
make auth-status
make model-status
make stop
```

확인 URL:

| 서비스 | URL |
|---|---|
| Gateway health | `http://localhost:9400/health` |
| Gateway Scalar UI | `http://localhost:9400/docs` |
| Gateway ReDoc | `http://localhost:9400/redoc` |
| Gateway OpenAPI JSON | `http://localhost:9400/openapi.json` |
| Risk Adapter health | `http://localhost:9405/health` |
| Risk Adapter Scalar UI | `http://localhost:9405/docs` |

`/docs`와 `/openapi.json`은 `specs/schemas/*.json`의 checked-in contract schema를 사용한다.

---

## Full-stack 경로 (GPU 필요)

Gateway, Risk Adapter, enabled vLLM runtime 3개, Prometheus, Grafana, DCGM exporter, cAdvisor를 compose로 함께 올린다.

**사전 준비:** Docker, NVIDIA Container Toolkit, GPU, Hugging Face token

**HF token 설정 (필수)**

`google/embeddinggemma-300m`은 Gemma 라이선스 동의가 필요한 gated 모델이다. 토큰이 없으면 embedding vLLM이 다운로드에 실패하고 `make ready`가 `not_ready`를 반환한다.

1. https://huggingface.co/google/gemma 에서 라이선스에 동의한다.
2. HuggingFace Settings → Access Tokens에서 토큰을 발급한다.
3. `.env`에 두 변수를 입력한다: `HF_TOKEN=hf_xxx`, `HUGGING_FACE_HUB_TOKEN=hf_xxx`

```bash
# .venv + deps + .env + validate + test + 플랫폼/risk 이미지 빌드 + risk config check
HF_TOKEN=hf_xxx AUTH_MODE=local_open make first-run

source .venv/bin/activate
make compose-up
make ready-full
make runtime-validate
make operator-reports
make release-check-full
make compose-down
```

`make compose-up`은 실행 전 `make preflight-compose`로 Docker, compose plugin, host-published 포트, GPU 표시 여부, `.runtime/prometheus/admin_api_key`를 확인한다. `.runtime/`만 손상되었으면 `make compose-up` 대신 `make sync-runtime-secrets`로 복구한다.

**재빌드·전체 초기화가 필요하면:**

```bash
make reset
HF_TOKEN=hf_xxx make rebuild-full
```

`make reset`은 `.env`와 upstream/base vLLM image를 기본 보존한다. base image까지 삭제하려면 `PURGE_BASE_IMAGES=1 make reset`을 명시한다.

---

## 인증과 보안 경계

| endpoint | 기본 의도 |
|---|---|
| `/health` | 단순 liveness. 공개 health check 용도 |
| `/ready` | dependency 상태 포함. non-local에서는 admin auth 또는 내부망 보호 필요 |
| `/metrics` | metric 상태 포함. Prometheus scrape 또는 내부망 보호 필요 |
| `/v1/*` | Gateway API key 필요 |
| Risk Adapter `/v1/*` | 내부 service token 필요 |

현재 인증 상태 확인:

```bash
make auth-status
make auth-doctor
make auth-plan MODE=strict
```

운영 기준: `local_open`은 로컬 개발용 비인증 모드이며, `private_network`, `edge_terminated`, `strict`는 public/admin/internal-service auth 경계를 구분한다. 인증 모드는 API 기능을 바꾸지 않고 접근 경계만 바꾼다. 상세 정책은 `docs/operations/auth_control_plane.md`, `docs/operations/admin_metrics_docs_exposure_policy.md`를 기준으로 한다.

---

## 모니터링

compose/staging/production-like 환경에서는 Prometheus, Grafana, DCGM exporter, cAdvisor를 기본 활성화한다.

| 서비스 | 포트 | 기본 상태 |
|---|---:|---|
| Prometheus | 9410 | 활성화 |
| Grafana | 9411 | 활성화 |
| DCGM exporter | 9412 | 활성화 |
| cAdvisor | 9413 | 활성화 |

Grafana 첫 화면은 `Serving Cockpit`을 기준으로 하며, service readiness, user traffic, scrape heartbeat, GPU warm residency, GPU headroom, OOM/restart 부재를 함께 확인한다. `GPU Capacity and OOM Risk`는 GPU/OOM drill-down으로 유지한다. 상세 설정은 `docs/operations/monitoring_ux.md`를 기준으로 한다.

---

## FastAPI Docs / ReDoc 정책

`/docs`, `/redoc`, `/openapi.json`은 `local_open`과 `private_network`에서는 활성화할 수 있지만 `edge_terminated`와 `strict`에서는 기본 비활성화한다. API shape를 노출하므로 public internet에 직접 노출하지 않는다.

| 서비스 | Scalar UI | ReDoc | OpenAPI JSON |
|---|---|---|---|
| Gateway | `http://<host>:9400/docs` | `http://<host>:9400/redoc` | `http://<host>:9400/openapi.json` |
| Risk Adapter | `http://<host>:9405/docs` | `http://<host>:9405/redoc` | `http://<host>:9405/openapi.json` |

문서 화면을 끄고 싶을 때만: `FASTAPI_DOCS_ENABLED=false`

상세 정책은 `docs/operations/admin_metrics_docs_exposure_policy.md`를 기준으로 한다.

---

## 모델 관리

모델별 리소스 제어 기준은 `configs/model_serving.yaml`, `configs/gpu_budgets.yaml`, `configs/monitoring.yaml`에 둔다. 사용자 조정 가능 파라미터는 `/v1/models` 응답의 `request_parameters`에 모델별로 노출한다.

```bash
make model-list
make model-status
make model-validate
make model-diff
```

모델 추가/제거는 바로 파일을 수정하지 않고 계획부터 만든다.

```bash
make model-propose-add ID=new-main PORT=9499 ENDPOINT=/v1/new-main UPSTREAM=org/model ROLE=main_llm
make model-propose-remove ID=local-main
```

이 명령은 실제 파일을 쓰지 않는다. id, port, endpoint, runtime service 충돌과 GPU budget 경고, 영향 파일 목록, 후속 검증 절차를 보여준다. 자세한 기준은 `docs/operations/model_parameter_discovery.md`를 참조한다.

---

## 빌드·재빌드·제거

| 하고 싶은 일 | 명령 | 서비스 기동 여부 |
|---|---|:---:|
| 정적 검증 | `make validate` | 아니오 |
| 테스트 | `make test` | 아니오 |
| 통합 파이프라인 빌드 | `make build-pipeline` / `make build` | 아니오 |
| 플랫폼 이미지만 재빌드 | `make rebuild-app` / `make build-image` | 아니오 |
| Risk vLLM 이미지만 재빌드 | `make rebuild-risk-vllm` | 아니오 |
| 전체 재빌드 | `make rebuild-full` / `make bootstrap` | 아니오 |
| 삭제 미리 보기 | `make remove-plan` / `make cleanup-plan` | 아니오 |
| 일반 산출물 제거 | `make clean` | 아니오 |
| 통합 제거/초기화 | `make reset` | 아니오 |

빌드/삭제/재빌드 의미론 상세: `docs/development/build_ux.md`

---

## 주요 명령 참조

| 명령 | 의미 |
|---|---|
| `make help` | 전체 명령 목록 |
| `make guide` | 상황별 명령 추천 가이드 |
| `make init-env-local` | 로컬 app-only `.env` 자동 생성 |
| `make init-env-compose` | full-stack compose용 `.env` 자동 생성 |
| `make validate` | OpenAPI·JSON Schema·YAML·포트·forbidden field invariant 검증 |
| `make test` | unit/contract test 실행 |
| `make build-pipeline` | validate + test + 플랫폼 이미지 + package (서비스 기동 없음) |
| `make first-run` / `make bootstrap` | 처음 full-stack 준비 |
| `make rebuild-full` | 전체 재빌드 (`make bootstrap` alias) |
| `make start` | 로컬 Gateway/Risk Adapter app-only 시작 |
| `make ready-local` | app-only `/health` 확인 |
| `make ready-full` | strict full-stack `/ready` + smoke 검증 |
| `make compose-up` | full-stack compose 시작 |
| `make compose-down` | full-stack compose 종료 |
| `make runtime-targets` | registry 기반 runtime target inventory |
| `make monitoring-projection` | Prometheus/Grafana projection |
| `make operator-reports` | runtime target·storage path·monitoring projection·operator status 통합 생성 |
| `make runtime-validate` | GPU/vLLM live evidence를 `reports/runtime/`에 생성 |
| `make release-check-full` | 정적 릴리스 gate + deterministic test suite |
| `make package` | release ZIP 생성 |
| `make remove-plan` | 삭제될 항목 미리 표시 |
| `make clean` | build/dist/cache/run 산출물 삭제 |
| `make reset` | 서비스 중지 + platform/risk image + 산출물 제거 |
| `make doctor` | Python/version/contracts/bash/env/status 진단 |
| `make auth-status` | 현재 인증 상태 확인 |
| `make auth-doctor` | 인증 설정 진단 |
| `make model-status` | 모델 registry 상태 |
| `make project-inventory` | 전체 파일·문서·관리 inventory |
| `make infisical-up` | 시크릿 관리 UI 기동 (선택) |

릴리스 ZIP 최상위 폴더명은 작업 디렉터리 이름과 무관하게 항상 `ai_model_serving_platform/`이다.

모델 캐시는 기본적으로 `HF_CACHE_DIR=./model_cache/huggingface`에 저장되고 vLLM 컨테이너 내부 `/root/.cache/huggingface`로 mount된다. 모델 캐시나 runtime secret까지 지우려면 실수 방지를 위해 명시한다.

```bash
PURGE_MODEL_CACHE=1 make clean-all
PURGE_RUNTIME_SECRETS=1 make clean-all
```

---

## 포트 참조

| 서비스 | 포트 | 역할 |
|---|---:|---|
| Gateway | 9400 | 외부 단일 진입점 |
| Main LLM vLLM | 9401 | Gemma 4 26B-A4B FP8 generation |
| Embedding vLLM | 9402 | EmbeddingGemma embeddings |
| Prompt vLLM | 9403 | Kanana Prompt detector |
| Risk Adapter | 9405 | risk signal 정규화/집계 |
| Prometheus | 9410 | metric 수집/조회 |
| Grafana | 9411 | 운영 dashboard |
| DCGM exporter | 9412 | GPU metric exporter |
| cAdvisor | 9413 | container metric exporter |

---

## 패키징

`make package`는 먼저 `make refresh-generated-reports`로 current inventory, runtime target, monitoring projection, operator status, static live evidence placeholder를 재생성한 뒤 ZIP을 만든다.

패키지에서 제외: `.env`, `.runtime/`, `model_cache/`, `logs/`, `dist/`, `__pycache__/`, timestamped `reports/runtime/runtime_validation_*.json|md`

패키지에 포함: `.env.example`, `.env.local.example`, `.env.compose.example`

```bash
make release-check-full
make package
```

---

## 운영 전 반드시 남은 검증

현재 패키지는 코드, 계약, 테스트, compose 예시, dashboard template을 포함한다. 실제 운영 확정 전에 target GPU host에서 다음 항목을 실측해야 한다.

| 항목 | 확인 내용 |
|---|---|
| vLLM 기동 | enabled runtime 3개가 정상 기동하는지 |
| VRAM | RTX 6000 Ada 48GB에서 peak/headroom이 충분한지 |
| latency | chat/embedding/risk detector p95/p99 |
| queue/timeout | 동시 요청에서 queue timeout과 circuit breaker 동작 |
| Prometheus | Gateway/Risk/vLLM/DCGM/cAdvisor scrape 정상 여부 |
| Grafana | 실제 runtime data가 dashboard에 렌더링되는지 |

```bash
make preflight-compose
make compose-up
make ready-full
make runtime-validate
```

---

## 문서 지도

| 목적 | 문서 |
|---|---|
| 전체 흐름 + 명령 선택 가이드 | `docs/operations/first_project_guide.md` |
| 빠른 시작 명령만 | `docs/operations/day0_quickstart.md` |
| 상황별 명령 선택 | `docs/operations/operator_workflows.md` |
| 빌드/삭제/패키징 의미론 | `docs/development/build_ux.md` |
| 인증 제어 | `docs/operations/auth_control_plane.md` |
| Admin/Metrics/Docs 노출 정책 | `docs/operations/admin_metrics_docs_exposure_policy.md` |
| 모니터링 UX | `docs/operations/monitoring_ux.md` |
| 서비스 URL·endpoint·모니터링 주소 | `docs/operations/endpoint_reference.md` |
| 모델 파라미터 | `docs/operations/model_parameter_discovery.md` |
| runtime validation URL/env 우선순위 | `docs/operations/runtime_validation_workplan.md#설정-우선순위` |
| 장애 진단 | `docs/operations/full_stack_troubleshooting.md` |
| 설정·관리·빌드·제거 통합 UX | `docs/operations/configuration_lifecycle.md` |
| 로컬 저장 경로·모델 캐시 위치 | `docs/operations/storage_paths.md` |
| GPU 리소스 계획 | `docs/resources/gpu_resource_plan.md` |
| 릴리스 버전 정책 | `docs/release/versioning_policy.md` |
| API 스펙 | `docs/specs/api.md` |
| Python 버전 호환성 | `docs/development/python_compatibility.md` |
| 테스트 전략 | `docs/development/test_strategy.md` |
| 릴리스 전 체크리스트 | `docs/development/final_checklist.md` |
| 로깅 정책 | `docs/development/logging_policy.md` |
| 아키텍처·설계 배경 | `docs/06_architecture.md`, `docs/01_project_background.md` |
| 결정 기록(ADR) | `docs/02_decision_register.md` |
| 문서 관리 정책 | `docs/governance/document_management.md` |

---

## 부록: 장애 진단

`make ready-full`이 실패하면 자동으로 compose diagnostics를 수집한다. 수동으로 다시 확인:

```bash
make compose-diagnostics
READY_MODE=full make status
make doctor
```

자주 보는 증상:

| 증상 | 먼저 볼 것 |
|---|---|
| app-only에서 `make ready` 실패 | `make ready-local` 사용 여부, `.env` profile |
| compose에서 Risk Adapter 접근 실패 | `make compose-diagnostics`, compose service 상태 |
| embedding model pull 실패 | `HF_TOKEN`, Gemma 라이선스 동의 |
| Prometheus admin token 오류 | `make sync-runtime-secrets` |
| Risk vLLM config 오류 | `make risk-vllm-config-check` |
| auth mismatch | `make auth-doctor` |

Embedding pooling runtime은 `max_num_batched_tokens >= max_model_len` 정책을 따른다. Kanana risk detector의 `bitsandbytes` 양자화는 기본 운영 정책이므로 임의로 제거하지 않는다.

---

## 부록: Kanana risk vLLM

`risk-prompt`는 main Gemma4 runtime과 image tag를 공유하지 않는다. `risk-prompt` 2.1B는 `hidden_size=1792`, `num_attention_heads=24`, `head_dim=128` 구조라서 explicit `head_dim`을 존중하는 runtime이 필요하다 (transformers 4.52.0–4.52.3 버그, 4.52.4에서 수정). `RISK_VLLM_IMAGE`는 `ops/docker/Dockerfile.risk-vllm-kanana`로 별도 빌드하며, `make first-run`이 image 내부 config 파싱까지 확인한다.

Risk detector에는 `--enforce-eager`가 적용된다. `max_num_seqs=1`, `max_output_tokens=1` 단일 토큰 분류기에서 CUDA graph 이점이 작고, vLLM 기동 중 CUDA graph capture 메모리 spike를 피하기 위해서다. `risk-siren`은 현재 retired 상태이며 기본 compose와 `/v1/models`에서 제외된다.

패치 lifecycle과 image metadata 정책은 `docs/operations/risk_vllm_patch_lifecycle.md`을 기준으로 검토한다.

```bash
make rebuild-risk-vllm
make risk-vllm-config-check
make risk-vllm-patch-removal-check
```

이 검사는 모델 weight를 로드하지 않고 HF AutoConfig만 확인한다.

---

## 부록: OpenAPI drift 점검

`python scripts/validation/openapi_snapshot_diff.py`는 strict auth 기준 generated OpenAPI와 `specs/openapi.*.yaml`의 path/method/security/summary/description/operationId/response status/schema drift를 검사한다. `make release-check`에도 포함된다.

---

## 부록: 레거시/불필요 산출물 정책

현재 플랫폼에 필요하지 않은 과거 원천 프로젝트 코드, 상세 inventory, cache, bytecode, fake runtime path는 release package에 포함하지 않는다. 기준 파일은 `docs/governance/policies/retired_source_cleanup_policy.md`와 `configs/retired_source_cleanup_policy.yaml`이다.

---

## 부록: 언어 정책

이 저장소의 기본 설명 언어는 **한국어**다. 운영자가 실제로 읽는 README와 주요 docs를 한국어 중심으로 관리한다.

영어 원문을 유지하는 대상:

| 유지 대상 | 예시 |
|---|---|
| API 경로 | `/docs`, `/redoc`, `/openapi.json`, `/v1/chat/completions` |
| 환경 변수 | `API_KEYS`, `ADMIN_API_KEY`, `FASTAPI_DOCS_ENABLED` |
| 명령어/제품명 | `make compose-up`, `docker compose`, `FastAPI`, `Prometheus`, `Grafana` |
| JSON/YAML field | `model`, `messages`, `risk_code`, `enabled` |
