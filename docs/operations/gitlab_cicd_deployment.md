# GitLab CI/CD 배포 가이드

이 프로젝트의 기본 `.gitlab-ci.yml`은 Docker executor + Docker-in-Docker Runner를 기준으로 한다. 기존 사내 `shell` executor Runner를 그대로 쓰려면 `image`, `services: docker:dind`, `DOCKER_HOST=tcp://docker:2375` 전제를 제거한 별도 CI 템플릿으로 분리해야 한다.

## Runner 전제

기본 Runner 요구사항:

- executor: `docker`
- tags: `ai-serving`, `docker-build`
- privileged: `true`
- Docker registry push 가능

Shell executor Runner를 사용할 때는 다음 조건을 맞춘다.

- tags를 실제 Runner tag로 변경한다.
- `docker info`가 host daemon에 직접 연결되어야 한다.
- `DOCKER_HOST=tcp://docker:2375`와 `docker:dind` service를 쓰지 않는다.

## Pipeline 정책

GitLab 12.1.1-ee 호환 구성이다. `workflow:`, `needs:`, `rules:`,
`artifacts:reports:dotenv` 키워드는 사용하지 않는다. 빌드한 platform image digest는
일반 artifact인 `build/platform-image.env`로 저장하고, deploy job이
`dependencies: [build-platform]`으로 내려받아 명시적으로 읽는다.

- `master`: `validate`, `unit-test`, `build-platform`
- `release` (rolling): `validate`, `unit-test`, `package-release`, `build-platform`, manual `deploy-gpu-175`
- `release` (vLLM-derived image build only): pipeline을 `BUILD_VLLM_DERIVED=1`로 시작 → `build-vllm-derived` 자동 포함, deploy mode는 바뀌지 않음
- `release` (full runtime deploy 준비): pipeline을 `DEPLOY_MODE=full`로 시작 → `build-vllm-derived` 자동 포함
- tag: `validate`, `unit-test`, `package-release`, `build-platform` + (`BUILD_VLLM_DERIVED=1` 또는 `DEPLOY_MODE=full`이면 `build-vllm-derived`)

`build-vllm-derived`는 `release`/tag ref에서 다음 중 하나가 설정된 pipeline에서 자동 실행된다.

- `BUILD_VLLM_DERIVED=1`
- `DEPLOY_MODE=full`

`build-vllm-derived`는 risk-vllm-kanana와 12B multimodal profile용 `vllm-gemma4-audio`를 함께 빌드·push한다. `embedding-ko-vllm`은 derived Dockerfile 빌드 대상이 아니라 `EMBEDDING_KO_VLLM_IMAGE`로 지정한 표준 vLLM 이미지를 사용한다. 실행된 job이 실패하면 release 실패로 처리된다(`allow_failure: false`). 빌드 로직은 `scripts/ci/build_vllm_derived_images.sh`에서 관리한다.

Platform image는 commit tag와 branch tag를 항상 push한다. `release` branch 또는 tag pipeline에서는 `VERSION` 파일을 읽어 `platform:release_<VERSION>` tag도 push한다.

## 필수 GitLab 변수

- `SSH_PRIVATE_KEY`: 175 배포 계정 private key, file-type variable
- `DEPLOY_KNOWN_HOSTS`: `ssh-keyscan <175-host>` 출력
- `DEPLOY_HOST`: 175 내부 DNS 또는 IP
- `DEPLOY_USER`: 175 SSH user
- `DEPLOY_PATH`: 175 배포 경로, 예: `/opt/acl-ai-gateway`
- `REGISTRY_DEPLOY_USER`: GitLab Deploy Token username, `read_registry`
- `REGISTRY_DEPLOY_PASSWORD`: GitLab Deploy Token password/token, `read_registry`

`REGISTRY_DEPLOY_USER/PASSWORD`가 없으면 GitLab 기본 `CI_REGISTRY_USER/PASSWORD`로 fallback한다. 운영 배포에는 read-only Deploy Token 사용을 우선한다.

## 선택 변수

- `DEPLOY_COMPOSE_FILE`: 기본값 `ops/compose/full-stack.private-network.yaml`
- `DEPLOY_MODE`: `rolling` 또는 `full`, 기본값 `rolling`
- `AUTH_MODE`: 배포 시 auth profile 적용. `local_open`, `private_network`, `strict` 등. 미설정 시 175 `.env` 현재값 유지
- `GATEWAY_BIND_ADDR`: Gateway host publish bind 주소
- `GATEWAY_HEALTH_URL`: 배포 후 health check URL
- `RUN_READY_SMOKE`: `1` 또는 `0`, 기본값 `1`
- `RUN_READY_FULL_SMOKE`: 호환성 변수이며 기본값은 `1`이다. `DEPLOY_MODE=full`에서는
  반드시 `1`이어야 하고 `make ready-full`을 항상 실행한다. `0`이면 배포 시작 전에 거절한다.
- `PRUNE_DANGLING_IMAGES`: `1` 또는 `0`, 기본값 `1`. 성공한 배포 뒤 태그가 사라진 dangling image만 정리
- `RISK_VLLM_IMAGE_TO_DEPLOY`: `DEPLOY_MODE=full`에서만 사용. risk vLLM image override가 필요할 때 175 `.env`의 `RISK_VLLM_IMAGE`를 해당 값으로 덮어쓴다
- `DEPLOY_RELEASE_ID`: `releases/<id>`에 사용할 immutable release ID. CI에서는 commit SHA를 사용한다.
- `RELEASES_TO_KEEP`: 보존할 성공 release 수, 기본값 `5`
- `COMPOSE_PROJECT_NAME`: `.env`에 저장되는 Docker Compose resource namespace.
  기존 설치 호환 기본값은 `compose`이며, 같은 host에 여러 설치가 있으면 고유값으로 설정한다.
- `HF_TOKEN`: validate 단계의 모든 main-model profile config/tokenizer canary에
  사용한다. 누락 또는 권한 부족을 job skip으로 숨기지 않고 validate 실패로
  처리한다. 배포 대상 host의 모델 다운로드는 원격 `.env`의 `HF_TOKEN`을 사용한다.

배포 파일은 운영 root를 직접 덮어쓰지 않는다. 새 소스는 먼저
`releases/<commit>`에 동기화되고 해당 디렉터리의 Compose·스크립트로 검증 및
기동된다. readiness가 성공한 뒤에만 `current` symlink를 원자적으로 교체한다.
Full 배포는 실제 vLLM 파일 세대를 나타내는 `runtime-current`도 함께 갱신한다.
실패하면 공유 `.env`, 서비스, `current`, `runtime-current`를 모두 이전 release로
복원한 후 후보 디렉터리를 삭제한다.

기존 운영 root를 직접 사용하던 서버의 첫 배포에서는 기존 트리를
`releases/legacy-<timestamp>`로 먼저 스냅샷하고 이를 초기 `current`와
`runtime-current`로 설정한다. 따라서 첫 release-directory Full 배포도 신규
스크립트가 아닌 legacy snapshot의 Compose와 Makefile을 rollback 원본으로 사용한다.

175의 `.env`에는 shared/staging 환경 기준으로 `GATEWAY_BIND_ADDR=<175 내부 IP>`를 명시하는 편이 안전하다. 전체 interface publish가 의도된 경우에만 `GATEWAY_BIND_ADDR=0.0.0.0`을 사용하고 firewall/network policy로 내부 CIDR만 허용한다. deploy smoke는 `GATEWAY_HEALTH_URL`이 없으면 175 `.env`의 `GATEWAY_BIND_ADDR`와 `GATEWAY_PORT`로 health URL을 만든다. `GATEWAY_BIND_ADDR=0.0.0.0`일 때만 `localhost`로 fallback한다. Rolling 배포는 `RUN_READY_SMOKE=1`로 Gateway `/health`를 확인하고, Full 배포는 추가로 `make ready-full`을 반드시 통과해야 한다.

배포 스크립트는 health check가 통과한 뒤 기본적으로 `docker image prune -f --filter dangling=true`를 실행한다. `release`처럼 같은 태그를 새 이미지가 덮어쓰면 이전 이미지가 `<none>` 상태로 남을 수 있는데, 이 단계는 실행 중인 컨테이너가 참조하지 않는 untagged image만 제거한다. 장애 분석이나 수동 롤백 때문에 보존이 필요하면 `PRUNE_DANGLING_IMAGES=0`으로 끈다.

## 배포 흐름

### Rolling app deploy (일반)

platform app 변경만 반영할 때 사용한다. vLLM-derived image는 재빌드하지 않는다.

1. `release` branch에 push → pipeline 자동 시작 (pipeline 변수 추가 없음)
2. `build-vllm-derived` 스킵 — `BUILD_VLLM_DERIVED` / `DEPLOY_MODE=full` 없으므로 조건 불충족
3. `deploy-gpu-175` 수동 실행 (기본 `DEPLOY_MODE=rolling`)
4. 175에서 `gateway`, `admin-sidecar`, `risk-adapter` image만 pull 후 재시작
5. vLLM 컨테이너는 건드리지 않으므로 GPU 모델 reload downtime 없음

### Build vLLM-derived images only

full deploy 없이 registry image만 미리 만들 때 사용한다. deploy mode를 바꾸지 않는다.

1. `release` branch 또는 tag pipeline을 `BUILD_VLLM_DERIVED=1`로 시작
2. `build-vllm-derived` 자동 실행
3. risk-vllm-kanana와 `vllm-gemma4-audio`를 build/push. audio image digest는 `build/audio-image.env`에 남긴다. `embedding-ko-vllm`은 표준 vLLM 이미지(`EMBEDDING_KO_VLLM_IMAGE`)를 사용하므로 별도 build 없음
4. deploy mode는 바뀌지 않는다
5. full deploy까지 하려면 `DEPLOY_MODE=full`을 사용해야 한다

### Full runtime deploy (vLLM 이미지 갱신 포함)

risk-vllm-kanana를 교체하거나 `vllm-gemma4-audio` digest를 새로 pin하거나 `EMBEDDING_KO_VLLM_IMAGE`(표준 vLLM 이미지)를 갱신할 때 사용한다.

1. pipeline을 `DEPLOY_MODE=full` 변수로 시작
3. `deploy-gpu-175` 수동 실행, `DEPLOY_MODE=full` 설정
   - preflight 실패 시 `.env`를 수정하지 않고 실패; `build-vllm-derived` 먼저 실행하라는 안내 출력
5. 전체 stack `up -d --remove-orphans`; vLLM 이미지 pull과 모델 로딩 시간이 길 수 있다
   - 배포 전 `.runtime/main-model/main-model-state.json`과 profile catalog를 검증한다.
   - locked profile 또는 저장된 active profile을 boot projection으로 생성한다.
   - 선택 profile의 고정 HF revision을 공용 cache에 준비한 뒤, 해당 profile로
     main runtime을 처음부터 부팅한다. 기본 26B를 먼저 올렸다가 재전환하지 않는다.
6. `make ready-full`을 반드시 실행해 Gateway `/ready`, downstream vLLM readiness,
   smoke를 함께 확인한다. 실패 시 compose diagnostics를 출력하고 `.env`와 Compose
   서비스 이미지를 배포 전 상태로 자동 복원한다. Full 배포는 변경 전에 실행 중인
   main-model container의 image/command도 실제 Compose project label로 캡처해
   rollback override로 사용한다. 자동 복원이 일부라도 실패하면 백업 경로와 수동
   복구 필요성을 오류로 남긴다.

## 배포 모드

`DEPLOY_MODE=rolling`은 app layer 배포다.

- `gateway`, `admin-sidecar`, `risk-adapter` image만 pull한다.
- `admin-sidecar`를 먼저 재시작한 뒤 `gateway`, `risk-adapter`를
  `up -d --no-deps`로 재시작한다.
- vLLM 모델 컨테이너를 건드리지 않아 GPU 모델 reload downtime을 피한다.

`DEPLOY_MODE=full`은 vLLM image 교체, runtime config(chat template·model profile·compose) 변경, 또는 초기 구축/stack drift 정렬용이다.

- compose 전체 image를 pull한다.
- **service 단위로 수렴한다(`compute_recreate_set`).** 실제로 바뀐 service만 재생성한다:
  - resolved image ID가 running 컨테이너와 다른 service (image 교체된 것)
  - `configs/main_model_profiles.yaml` 또는 `configs/gemma4_chat_template.jinja`가
    직전 릴리즈와 다르면 → **`main-llm-vllm`만** (configs/와 chat template을
    mount하는 유일한 모델)
  - compose 파일 자체가 바뀌면 → 전체 service (구조 변경은 어떤 service 정의든
    바꿀 수 있고 image ID로는 감지되지 않으므로 보수적으로 전부 재수렴)
- 재생성은 항상 `--no-deps`로 한다. 안 그러면 `up -d gateway`가 gateway의
  `depends_on` 그래프(vLLM 전체)를 끌어오고, shared `.env`가 매 배포 config-hash를
  흔들어 결국 fleet 전체가 recreate된다.
- 바뀌지 않은 vLLM 모델은 그대로 serving을 유지하므로, platform-only 변경이
  full로 분류되어도 모델을 cold-restart하지 않는다.
- 롤백도 동일한 `compute_recreate_set`을 실패 후보(RELEASE_PATH) 기준으로 호출해
  **바뀐 service만 대칭으로 되돌린다** — fleet 전체 재기동이나 split 상태가 없다.
- 처음 기동이거나 stack이 내려가 있으면 모든 service가 "not running"으로 잡혀
  전부 기동된다(정당한 cold start). 평상시 healthy stack에서는 변경분만 건드린다.

> 참고: 모든 service가 `env_file: ../../.env`로 shared `.env`를 통째로 로드하기
> 때문에, deploy마다 바뀌는 키(`PLATFORM_IMAGE` digest, `DEPLOY_RELEASE_ID` 등)로
> Compose의 config-hash가 매번 전 service에서 흔들린다. 그래서 수렴 판단은
> config-hash가 아니라 **resolved image ID 비교**(`.env` 잡음에 영향받지 않고,
> 같은 tag가 새 digest로 재빌드된 경우도 잡음)로 한다.

## 동기화 범위

배포 스크립트는 프로젝트 파일 전체를 rsync하되 runtime/generated 경로는 제외한다. 따라서 compose뿐 아니라 Prometheus rule, Grafana dashboard/provisioning, scripts, Makefile, VERSION 변경이 175에 함께 반영된다.

제외되는 대표 경로:

- `.env`
- `.runtime/`
- `.venv/`
- `model_cache/`
- `models/`
- `logs/`
- `dist/`
- generated cache/output 경로

## 175 최초 준비

175에는 먼저 패키지를 풀고 `.env`와 runtime secret을 만든다.

```bash
make init-env-compose
```

그다음 운영자가 175 환경에 맞는 값을 `.env`에 명시한다.

```text
GATEWAY_BIND_ADDR=<175 internal IP>
GATEWAY_PORT=9400
PLATFORM_IMAGE=<registry>/platform:<validated tag>
```

초기 전체 기동은 `DEPLOY_MODE=full` 또는 175 로컬에서 다음 명령을 사용한다.

```bash
make compose-up-private
```

평상시 platform app 변경 배포는 GitLab `deploy-gpu-175` job에서 `DEPLOY_MODE=rolling`을 사용한다.

## Dense retrieval-ko Artifact Runbook


```text
```

피해야 할 값:

```text
```



서버에서 사전 준비가 필요하면 host Python 대신 검증된 platform image container를 사용한다:

```bash
sudo mkdir -p /opt/acl-ai-gateway/models
sudo chown -R "$USER:$USER" /opt/acl-ai-gateway/models

HF_CACHE_DIR_HOST=/opt/acl-ai-gateway/ops/compose/model_cache/huggingface
PLATFORM_IMAGE_TO_DEPLOY=<registry>/platform:<tested-tag>


docker run --rm \
  -v "${HF_CACHE_DIR_HOST}:/root/.cache/huggingface" \
  "${PLATFORM_IMAGE_TO_DEPLOY}" \
    --output-dir /out

MODEL_DIR=/opt/acl-ai-gateway/models/embedding-ko-vllm
test -f "$MODEL_DIR/config.json"
test -f "$MODEL_DIR/proj.pt"
test -d "$MODEL_DIR/tokenizer"
test -f "$MODEL_DIR/encoder/config.json"
test -f "$MODEL_DIR/encoder/model.safetensors"
```

그 다음 175 `.env`에 다음을 설정한다.

```text
```

Artifact를 배포 중 명시적으로 준비하려면 pipeline/job 변수로 다음을 설정한다.

```text
DEPLOY_MODE=full
```



```bash
docker run --rm \
  -v "${HF_CACHE_DIR_HOST}:/root/.cache/huggingface" \
  "${PLATFORM_IMAGE_TO_DEPLOY}" \
    --output-dir /out
```


`HF_CACHE_DIR`가 절대경로이면 deploy script는 그대로 사용한다. 상대경로이면 `DEPLOY_PATH` root가 아니라 compose file directory 기준으로 해석한다. 예:

```text
DEPLOY_PATH=/opt/acl-ai-gateway
COMPOSE_FILE=ops/compose/full-stack.private-network.yaml
HF_CACHE_DIR=./model_cache/huggingface
resolved path=/opt/acl-ai-gateway/ops/compose/model_cache/huggingface
```


## 로컬 빌드와 CI 빌드 분리

CI job과 로컬 make target은 목적이 다르며 독립적으로 실행된다.

| 목적 | CI job / 로컬 명령 | 변수 기반 |
|---|---|---|
| Platform 이미지 빌드 + push | `build-platform` (CI) | `CI_REGISTRY_IMAGE` 등 CI 변수 |
| 로컬 risk vLLM 이미지 빌드 | `make build-risk-vllm-image` | `.env`의 `RISK_VLLM_BASE_IMAGE`, `RISK_VLLM_IMAGE` |
| 12B multimodal derived 이미지 빌드/push | `build-vllm-derived` 또는 `ops/images/vllm-gemma4-audio/README.md` 수동 fallback | `VLLM_BASE_IMAGE`, `AUDIO_VLLM_IMAGE_*` |



derived Dockerfile: `ops/docker/Dockerfile.risk-vllm-kanana`(risk-vllm-kanana 전용), `ops/images/vllm-gemma4-audio/Dockerfile`(12B multimodal image/audio/video profile 전용). `embedding-ko-vllm`은 표준 vLLM 이미지를 사용하며 derived Dockerfile이 없다.

운영 원칙:

- CI/CD: release, registry push, deploy orchestration
- Local: CI 없이 build/test/compose 재현
- Shared: Dockerfile, compose validation, model contract

로컬 빌드 명령과 CI/릴리스 빌드 절차 전반은 [`docs/development/build_ux.md`](../development/build_ux.md)를 기준으로 한다.

## 배포 오류 대응

### `main-model state file exists but is not readable by the deploy user`

```
[deploy] ERROR: main-model state file exists but is not readable by the deploy user.
[deploy]   Fix: sudo chmod o+r /opt/acl-ai-gateway/.runtime/main-model/main-model-state.json
[deploy]   This happens when the admin-sidecar container wrote the file as a different user.
```

**원인**

`admin-sidecar` 컨테이너는 `user: 0:0`(root)으로 실행되며, state 파일을 쓸 때 기본 권한 `0o600`(소유자만 읽기 가능)으로 생성한다. deploy 사용자는 root가 아니기 때문에 파일을 읽지 못한다.

코드 수정(`_write_unlocked`에서 `os.chmod 0o644` 적용) 이후 새로 쓰는 파일은 자동으로 `0o644`로 생성된다. 단, **수정 전 코드가 쓴 파일이 서버에 남아 있으면** 최초 1회 수동 조치가 필요하다.

**조치**

175 서버에 접속해서 다음을 실행한다.

```bash
sudo chmod o+r /opt/acl-ai-gateway/.runtime/main-model/main-model-state.json
```

이후 파이프라인을 재트리거하면 된다. 이 조치는 한 번만 필요하다.

---

### `Invalid or missing PLATFORM_IMAGE_DIGEST artifact`

```
Invalid or missing PLATFORM_IMAGE_DIGEST artifact
```

**원인**

`deploy-gpu-175` job이 `build-platform` job의 아티팩트(`build/platform-image.env`)를 찾지 못한 경우다. `build-platform`이 실패했거나, 아티팩트 만료(7일) 후 이전 pipeline의 deploy job을 재실행하는 경우에 발생한다.

**조치**

`build-platform` job부터 다시 실행하거나, 새 pipeline을 트리거한다.

---

### 배포 후 자동 롤백된 경우

```
[deploy] previous release, .env, services, and release links restored
[deploy] ERROR: candidate release context is invalid
```

`make ready-full`(health → ready → smoke test) 중 하나가 실패하면 자동으로 이전 릴리즈로 롤백된다. 롤백 자체는 정상 동작이다.

**원인 파악**

로그에서 롤백 직전 출력을 확인한다. 주요 원인:

| 증상 | 원인 | 조치 |
|------|------|------|
| smoke test POST 503 | vLLM이 `/ready` 200 반환 후 실제 추론 준비 미완료 (race condition) | 재트리거. `SMOKE_RETRY_ATTEMPTS`, `SMOKE_RETRY_DELAY_SECONDS` 조정 가능 |
| `/health` timeout | 컨테이너 기동 실패 또는 image pull 오류 | `make compose-diagnostics`로 서비스별 로그 확인 |
| `ready-full` timeout | vLLM 모델 로딩 지연 (최초 다운로드, quantization 초기화) | `READY_FULL_TIMEOUT_SECONDS=2700` 설정 후 재트리거 |
| `main-model chat not serving ... gate did not open` | control-plane 재배포로 admin-sidecar 재기동 → main-model gate가 boot reconcile 동안 닫힘 (`local-main` chat 503). gate 대기 budget은 모델 로딩과 동일한 `READY_FULL_TIMEOUT_SECONDS`(기본 1800s) | `observed == target`이면 수초 내 reopen. `observed != target`(persisted profile ≠ 실행 중 main-llm)이면 **모델 swap = 전체 reload**라 분 단위로 걸림(1800s budget이 커버). budget 초과 시 CI 로그의 `make compose-diagnostics` 출력에서 admin-sidecar boot reconcile 로그 확인 — 매 배포 swap이 일어나면 boot_profile 산출이 불안정한 것 |
