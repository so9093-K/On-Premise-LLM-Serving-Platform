# Day-0 빠른 시작 UX

이 문서는 처음 패키지를 받은 운영자 또는 개발자가 혼동하기 쉬운 실행 경로를 분리한다.

서비스 접근 주소·API endpoint·모니터링 URL은 **[Endpoint Reference](./endpoint_reference.md)** 를 참조한다. 전체 배경과 체크리스트는 **[처음 프로젝트를 받았을 때 전체 가이드](./first_project_guide.md)** 를 먼저 본다. 상황별 명령 선택은 `make guide` 또는 **[Operator Workflow Guide](./operator_workflows.md)** 를 참고한다.

## 1. App-only 확인

vLLM/GPU 없이 Gateway와 Risk Adapter process만 확인한다.

**반드시 `make init-env-local`을 사용한다.** `make init-env-compose`로 만든 `.env`는 `RISK_ADAPTER_BASE_URL=http://risk-adapter:9405` 같은 compose 내부 hostname을 사용하므로, app-only 모드에서 `make ready`가 실패한다. app-only readiness는 `make ready-local`만 사용한다.

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
make stop
```

`make ready-local`은 Gateway/Risk Adapter `/health`를 strict하게 확인한다. 두 app process 중 하나라도 내려가 있으면 실패한다. 이 모드에서 `/ready`가 `not_ready`인 것은 vLLM이 없을 때 정상이다.

## 2. Full-stack 확인

Docker, NVIDIA runtime, 실제 vLLM 모델 runtime이 있는 host에서 수행한다.

```bash
# .venv 생성 + 의존성 설치 + .env 초기화 + validate + test
# + 플랫폼 이미지 빌드 + Kanana risk vLLM 이미지 빌드 + risk config check
# google/embeddinggemma-300m은 Gemma 라이선스 동의 필요 → HF_TOKEN 필수
HF_TOKEN=hf_xxx make first-run

source .venv/bin/activate
make compose-up
make ready-full
make runtime-validate
make operator-reports
make compose-down
```

인증 없이 바로 올리려면 `AUTH_MODE=local_open`을 앞에 붙인다. `.env` 초기화 직후 자동으로 `local_open` 프로파일(API key 불필요, APP_ENV=local)이 적용된다.

**모든 포트를 허용하는 운영 세팅 (master_open):**

vLLM, Risk Adapter, Prometheus, Grafana 등 전체 stack을 host-published로 올릴 때 사용한다.

```bash
make init-env-compose
make auth-apply MODE=private_network      # API key 인증 활성화
make exposure-apply MODE=master_open AUDIENCE=private_lan  # 전체 포트 허용 (내부망)
make compose-up
make exposure-status                       # 현재 노출 상태 확인
```

AUDIENCE 선택 기준:

| 값 | 의미 |
|---|---|
| `local_only` | 127.0.0.1 바인드 (개발자 로컬) |
| `private_lan` | 사내 사설망 (staging·ops 표준) |
| `vpn` | VPN 경계 내부 |
| `public` | 인터넷 노출 (권장하지 않음) |

```bash
AUTH_MODE=local_open HF_TOKEN=hf_xxx make first-run

source .venv/bin/activate
make compose-up
make ready
```

`make first-run`은 `make bootstrap`의 alias다. `make bootstrap`은 `.venv` 생성부터 플랫폼 이미지와 Kanana risk 전용 vLLM 이미지 빌드, image 내부 Kanana config check까지 한 번에 처리한다. 기존 `.env`에 `HF_TOKEN`이 이미 있어도 `HF_TOKEN=hf_xxx`를 명시하면 항상 덮어쓴다.

bootstrap 완료 후 자동으로 수행되는 동작:
- 스택이 이미 실행 중이면 `gateway`, `risk-adapter`를 재시작해 갱신된 토큰을 반영한다.
- Infisical이 설정되어 있으면 새 시크릿을 Infisical에 자동 push한다.
- `.env`에 `SECRETS_GENERATED_AT` 타임스탬프를 기록해 마지막 갱신 시각을 추적한다.

`make ready-full`은 실제 upstream vLLM까지 준비되어야 성공한다. app-only 개발 환경에서는 실패가 정상이다.

## 3. 실패 시 첫 진단

```bash
make doctor
make status
READY_MODE=full make status
```

- `python` 명령이 없으면 `python3.12`를 사용한다. Makefile은 자동으로 탐지한다.
- `make ready`가 `risk-adapter /health: unavailable HTTP 000`을 반환하면 compose hostname 충돌이다. app-only 모드에서는 `make ready-local`을 사용한다.
- 포트가 busy면 기존 process를 종료하거나 `.env`의 port를 조정한다.
- `.runtime/prometheus/admin_api_key`만 없으면 `.env`를 재생성하지 말고 `make sync-runtime-secrets`를 실행한다.

## 4. 전체 초기화 + 재빌드 UX

환경 오염, 머신 이관, 릴리스 전 완전 초기화가 필요할 때 사용한다.

`make reset`은 compose service와 platform image까지 지우므로 Docker daemon 접근 권한이 필요하다. `docker info`가 permission denied이면 먼저 Docker 권한을 복구한다. 이 상태에서 reset을 계속하면 컨테이너/이미지는 남고 `.venv`, `.runtime`, `model_cache`만 지워지는 partial reset이 될 수 있으므로, reset script는 Docker 접근 실패 시 로컬 파일 삭제 전에 중단한다.

`make first-run`/`make rebuild-full`/`make bootstrap`은 마지막에 platform image와 Kanana risk vLLM image를 빌드하므로 Docker daemon 접근 권한이 필요하다. Docker 권한이 없으면 venv를 새로 만들기 전에 중단한다.

### 재빌드 절차

**인증 있음 (staging/production 기본값)**

```bash
# 1. 정리
make compose-down
PURGE_MODEL_CACHE=1 PURGE_RUNTIME_SECRETS=1 PURGE_VENV=1 make reset

# 2. 재빌드 (.venv + deps + .env 재생성 + validate + test + 이미지 빌드)
HF_TOKEN=hf_xxx make rebuild-full

# 3. 기동
source .venv/bin/activate
make compose-up
make ready
```

**인증 없음 (개발·테스트 환경)**

`AUTH_MODE=local_open`을 앞에 붙이면 `.env` 초기화 직후 자동으로 `local_open` 프로파일이 적용된다. `make auth-apply`를 별도로 실행할 필요가 없다.

```bash
# 1. 정리
make compose-down
PURGE_MODEL_CACHE=1 PURGE_RUNTIME_SECRETS=1 PURGE_VENV=1 make reset

# 2. 재빌드 + 인증 비활성화
AUTH_MODE=local_open HF_TOKEN=hf_xxx make rebuild-full

# 3. 기동
source .venv/bin/activate
make compose-up
make ready
```

`AUTH_MODE=local_open`이 적용되면 다음이 자동으로 설정된다:

| 항목 | 값 |
|---|---|
| `APP_ENV` | `local` |
| `API_KEY_REQUIRED` | `false` |
| `ADMIN_API_KEY_REQUIRED` | `false` |
| `INTERNAL_SERVICE_AUTH_REQUIRED` | `false` |

**HF_TOKEN 처리:**
- `HF_TOKEN=hf_xxx`를 명시하면 `.env`에 기존 값이 있어도 항상 덮어쓴다.
- `.env`가 이미 유효한 토큰을 갖고 있으면 `HF_TOKEN=` 없이 `make rebuild-full`만 실행해도 된다.
- 토큰이 없으면 bootstrap은 경고 후 계속 진행하지만, `make compose-up` 시 `google/embeddinggemma-300m` pull이 실패한다.

| 플래그 | 기본값 | 삭제 대상 |
|---|---|---|
| `PURGE_MODEL_CACHE=1` | 0 | `model_cache/`, legacy `ops/compose/model_cache/` |
| `PURGE_RUNTIME_SECRETS=1` | 0 | `.runtime/` |
| `PURGE_VENV=1` | 0 | `.venv/` |
| `PURGE_BASE_IMAGES=1` | 0 | upstream/base vLLM images |

로컬 `RISK_VLLM_IMAGE`는 `make reset`이 삭제한다. upstream/base vLLM 이미지는 수십 GB이므로 기본 보존하며, 필요할 때만 `PURGE_BASE_IMAGES=1 make reset`으로 삭제한다.

## 5. 삭제 UX

**정리 범위별 명령 선택:**

| 삭제 대상 | 명령 |
|---|---|
| 빌드 아티팩트만 (`dist/`, `__pycache__` 등) | `make clean` |
| 아티팩트 + 로그 | `make clean-all` |
| 아티팩트 + 로그 + 모델 캐시 | `PURGE_MODEL_CACHE=1 make clean-all` |
| **Docker 이미지 포함 전체** | `make reset` |
| **전체 초기화** (이미지 + venv + 시크릿 + 모델) | `PURGE_MODEL_CACHE=1 PURGE_RUNTIME_SECRETS=1 PURGE_VENV=1 make reset` |

> `make clean` / `make clean-all`은 **Docker 이미지를 삭제하지 않는다.** 이미지까지 정리하려면 `make reset`을 사용한다.

`make clean`과 `make clean-all`은 로컬 서비스가 실행 중이면 기본적으로 중단한다. 먼저 `make stop`을 실행한다.

삭제 전 미리 보기:
```bash
make remove-plan     # 또는 make cleanup-plan
```

`make remove-plan`과 `make cleanup-plan`은 `make clean-dry-run`의 읽기 쉬운 alias다.


## 6. 시크릿 관리 Infisical (선택)

API 토큰·비밀번호를 웹 UI에서 조회·관리하고 감사 로그를 남기고 싶다면 Infisical 자체 호스팅 스택을 추가로 기동한다. 메인 AI 서빙 스택과 분리된 독립 스택이므로, 설정하지 않아도 서비스 운영에 영향이 없다.

### 초기 설정 (최초 1회)

```bash
# 1. compose용 .env 생성
#    INFISICAL_AUTH_SECRET / INFISICAL_ENCRYPTION_KEY 는 여기서 자동 생성된다.
make init-env-compose

# 2. Infisical 스택 기동
make infisical-up
# → 웹 UI: http://localhost:9420

# 3. 웹 UI에서 계정·프로젝트·Machine Identity 설정
make infisical-init     # 단계별 가이드 출력

# 4. .env에 CLIENT_ID, CLIENT_SECRET, PROJECT_ID 입력 후 동기화
make secrets-push
```

> **주의:** `INFISICAL_AUTH_SECRET`, `INFISICAL_ENCRYPTION_KEY`는 최초 생성 후 절대 변경하지 않는다. 변경 시 기존 시크릿 복호화 불가.

### 일상 운영

```bash
make secrets-status     # .env vs Infisical 상태 비교
make secrets-push       # 토큰 갱신 후 Infisical에 반영
make secrets-pull       # Infisical에서 .env 갱신
```

`make first-run`/`make bootstrap` 실행 시 Infisical이 설정되어 있으면 토큰 갱신 → `.env` 기록 → Infisical 자동 push → `gateway`/`risk-adapter` 재시작까지 한 번에 처리된다.

---

## 7. Runtime secret directory와 테스트

`make init-env-compose`는 `.runtime/prometheus/admin_api_key`를 생성합니다. `.runtime/`은 로컬 compose secret directory이므로 작업 트리에 존재할 수 있습니다. `make clean-all`은 안전을 위해 기본적으로 `.runtime`을 보존합니다. 완전 재생성이 필요할 때만 `PURGE_RUNTIME_SECRETS=1 make clean-all`을 사용하세요. 릴리스 ZIP과 source handoff ZIP에는 `.runtime/`이 포함되면 안 됩니다.
