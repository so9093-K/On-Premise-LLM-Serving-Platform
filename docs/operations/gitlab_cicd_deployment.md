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

GitLab 12.1.1-ee 호환 구성이다. `workflow:`, `needs:`, `rules:` 키워드는 지원하지 않아 사용하지 않는다.

- `master`: `validate`, `unit-test`, `build-platform`
- `release` (rolling): `validate`, `unit-test`, `package-release`, `build-platform`, manual `deploy-gpu-175`
- `release` (vLLM-derived image build only): pipeline을 `BUILD_VLLM_DERIVED=1`로 시작 → `build-vllm-derived` 자동 포함, deploy mode는 바뀌지 않음
- `release` (full runtime deploy 준비): pipeline을 `DEPLOY_MODE=full`로 시작 → `build-vllm-derived` 자동 포함
- tag: `validate`, `unit-test`, `package-release`, `build-platform` + (`BUILD_VLLM_DERIVED=1` 또는 `DEPLOY_MODE=full`이면 `build-vllm-derived`)

`build-vllm-derived`는 `release`/tag ref에서 다음 중 하나가 설정된 pipeline에서 자동 실행된다.

- `BUILD_VLLM_DERIVED=1`
- `DEPLOY_MODE=full`

risk-vllm-kanana와 colbert-ko-vllm(required dedicated retrieval runtime)을 단일 job에서 순차 빌드한다. vLLM base image(~25 GB)를 한 번만 pull해 두 이미지가 daemon layer cache를 공유한다. 실행된 job이 실패하면 release 실패로 처리된다(`allow_failure: false`). 빌드 로직은 `scripts/ci/build_vllm_derived_images.sh`에서 관리한다.

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
- `PRUNE_DANGLING_IMAGES`: `1` 또는 `0`, 기본값 `1`. 성공한 배포 뒤 태그가 사라진 dangling image만 정리
- `PREPARE_COLBERT_KO_ARTIFACT`: `1`이면 `DEPLOY_MODE=full` preflight 전에 `PLATFORM_IMAGE_TO_DEPLOY` 컨테이너 안에서 ColBERT-ko prepared artifact를 명시적으로 생성한다. 기본값은 자동 준비하지 않음
- `RISK_VLLM_IMAGE_TO_DEPLOY`: `DEPLOY_MODE=full`에서만 사용. risk vLLM image override가 필요할 때 175 `.env`의 `RISK_VLLM_IMAGE`를 해당 값으로 덮어쓴다
- `COLBERT_KO_VLLM_IMAGE_TO_DEPLOY`: `DEPLOY_MODE=full`에서만 사용. colbert-ko-vllm image를 새 CI 태그로 교체할 때 175 `.env`의 `COLBERT_KO_VLLM_IMAGE`를 해당 값으로 덮어쓴다

175의 `.env`에는 shared/staging 환경 기준으로 `GATEWAY_BIND_ADDR=<175 내부 IP>`를 명시하는 편이 안전하다. 전체 interface publish가 의도된 경우에만 `GATEWAY_BIND_ADDR=0.0.0.0`을 사용하고 firewall/network policy로 내부 CIDR만 허용한다. deploy smoke는 `GATEWAY_HEALTH_URL`이 없으면 175 `.env`의 `GATEWAY_BIND_ADDR`와 `GATEWAY_PORT`로 health URL을 만든다. `GATEWAY_BIND_ADDR=0.0.0.0`일 때만 `localhost`로 fallback한다.

배포 스크립트는 health check가 통과한 뒤 기본적으로 `docker image prune -f --filter dangling=true`를 실행한다. `release`처럼 같은 태그를 새 이미지가 덮어쓰면 이전 이미지가 `<none>` 상태로 남을 수 있는데, 이 단계는 실행 중인 컨테이너가 참조하지 않는 untagged image만 제거한다. 장애 분석이나 수동 롤백 때문에 보존이 필요하면 `PRUNE_DANGLING_IMAGES=0`으로 끈다.

## 배포 흐름

### Rolling app deploy (일반)

platform app 변경만 반영할 때 사용한다. vLLM-derived image는 재빌드하지 않는다.

1. `release` branch에 push → pipeline 자동 시작 (pipeline 변수 추가 없음)
2. `build-vllm-derived` 스킵 — `BUILD_VLLM_DERIVED` / `DEPLOY_MODE=full` 없으므로 조건 불충족
3. `deploy-gpu-175` 수동 실행 (기본 `DEPLOY_MODE=rolling`)
4. 175에서 `gateway`, `risk-adapter` image만 pull 후 재시작
5. vLLM 컨테이너는 건드리지 않으므로 GPU 모델 reload downtime 없음
6. `RISK_VLLM_IMAGE_TO_DEPLOY` 또는 `COLBERT_KO_VLLM_IMAGE_TO_DEPLOY`가 설정되어 있으면 fail-fast한다

### Build vLLM-derived images only

full deploy 없이 registry image만 미리 만들 때 사용한다. deploy mode를 바꾸지 않는다.

1. `release` branch 또는 tag pipeline을 `BUILD_VLLM_DERIVED=1`로 시작
2. `build-vllm-derived` 자동 실행
3. risk-vllm-kanana와 colbert-ko-vllm(required dedicated retrieval runtime)을 build/push
4. deploy mode는 바뀌지 않는다
5. full deploy까지 하려면 `DEPLOY_MODE=full`을 사용해야 한다

### Full runtime deploy (vLLM 이미지 갱신 포함)

risk-vllm-kanana 또는 colbert-ko-vllm(required dedicated retrieval runtime)을 교체할 때 사용한다.

1. pipeline을 `DEPLOY_MODE=full` 변수로 시작
2. `build-vllm-derived` 자동 실행 → risk/colbert 이미지 빌드 후 registry push
3. `deploy-gpu-175` 수동 실행, `DEPLOY_MODE=full` 설정
   - `RISK_VLLM_IMAGE_TO_DEPLOY` / `COLBERT_KO_VLLM_IMAGE_TO_DEPLOY` 미설정 시 `RISK_VLLM_IMAGE_SHA`와 `COLBERT_KO_VLLM_IMAGE_SHA`가 기본 배포 image
4. 175에서 `.env` 수정 전 platform/risk/colbert 이미지 pull 가능 여부 검증 (preflight)
   - ColBERT-ko prepared artifact도 `.env` 수정 전 검증한다. `COLBERT_KO_MODEL_DIR`는 절대경로여야 하며 root에 `config.json`, `proj.pt`, `tokenizer/`, `encoder/config.json`, `encoder/model.safetensors`가 있어야 한다
   - preflight 실패 시 `.env`를 수정하지 않고 실패; `build-vllm-derived` 먼저 실행하라는 안내 출력
5. 전체 stack `up -d --remove-orphans`; vLLM 이미지 pull과 모델 로딩 시간이 길 수 있다

## 배포 모드

`DEPLOY_MODE=rolling`은 app layer 배포다.

- `gateway`, `risk-adapter` image만 pull한다.
- `gateway`, `risk-adapter`만 `up -d --no-deps`로 재시작한다.
- vLLM 모델 컨테이너를 건드리지 않아 GPU 모델 reload downtime을 피한다.
- `RISK_VLLM_IMAGE_TO_DEPLOY` / `COLBERT_KO_VLLM_IMAGE_TO_DEPLOY`는 허용하지 않는다.

`DEPLOY_MODE=full`은 초기 구축 또는 stack drift 정렬용이다.

- compose 전체 image를 pull한다.
- 전체 stack을 `up -d --remove-orphans`로 정렬한다.
- vLLM image pull과 모델 로딩 시간이 길 수 있다.
- `.env` 수정 전에 platform/risk/colbert image pull preflight를 수행한다.
- `.env` 수정 전에 `COLBERT_KO_MODEL_DIR` prepared artifact preflight를 수행한다.

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

## ColBERT-ko Artifact Runbook

`COLBERT_KO_MODEL_DIR`는 production full deploy에서 절대경로여야 한다. 권장값:

```text
COLBERT_KO_MODEL_DIR=/opt/acl-ai-gateway/models/colbert-ko-vllm
```

피해야 할 값:

```text
COLBERT_KO_MODEL_DIR=./models/colbert-ko-vllm
```

`sigridjineth/colbert-ko-embeddinggemma-300m` 원본 repository 또는 raw Hugging Face cache를 vLLM `--model` 경로로 직접 마운트하지 않는다. 원본 source repo는 prepared artifact의 source일 뿐이며, repo/root cache가 반드시 vLLM-compatible model directory는 아니다. Deployment에서 사용하는 `COLBERT_KO_MODEL_DIR`는 raw HF cache가 아니라 `scripts/models/prepare_colbert_ko_vllm_artifact.py`가 생성한 prepared output directory다.

현재 compose command는 `--model /models/colbert-ko-vllm`이다. 따라서 host의 `$COLBERT_KO_MODEL_DIR/config.json`이 컨테이너의 `/models/colbert-ko-vllm/config.json`으로 보여야 한다. prepared artifact root에는 최소 `config.json`, `proj.pt`, `tokenizer/`, `encoder/config.json`, `encoder/model.safetensors`가 있어야 한다.

서버에서 사전 준비가 필요하면 host Python 대신 검증된 platform image container를 사용한다:

```bash
sudo mkdir -p /opt/acl-ai-gateway/models
sudo chown -R "$USER:$USER" /opt/acl-ai-gateway/models

COLBERT_KO_MODEL_DIR=/opt/acl-ai-gateway/models/colbert-ko-vllm
HF_CACHE_DIR_HOST=/opt/acl-ai-gateway/model_cache/huggingface
PLATFORM_IMAGE_TO_DEPLOY=<registry>/platform:<tested-tag>

mkdir -p "$COLBERT_KO_MODEL_DIR" "$HF_CACHE_DIR_HOST"

docker run --rm \
  -v "${COLBERT_KO_MODEL_DIR}:/out" \
  -v "${HF_CACHE_DIR_HOST}:/root/.cache/huggingface" \
  "${PLATFORM_IMAGE_TO_DEPLOY}" \
  python scripts/models/prepare_colbert_ko_vllm_artifact.py \
    --output-dir /out

MODEL_DIR=/opt/acl-ai-gateway/models/colbert-ko-vllm
test -f "$MODEL_DIR/config.json"
test -f "$MODEL_DIR/proj.pt"
test -d "$MODEL_DIR/tokenizer"
test -f "$MODEL_DIR/encoder/config.json"
test -f "$MODEL_DIR/encoder/model.safetensors"
```

그 다음 175 `.env`에 다음을 설정한다.

```text
COLBERT_KO_MODEL_DIR=/opt/acl-ai-gateway/models/colbert-ko-vllm
```

Artifact를 배포 중 명시적으로 준비하려면 pipeline/job 변수로 다음을 설정한다.

```text
DEPLOY_MODE=full
PREPARE_COLBERT_KO_ARTIFACT=1
```

`PREPARE_COLBERT_KO_ARTIFACT=1`이 없으면 deploy는 대용량 모델 다운로드를 암묵적으로 시작하지 않는다. 운영자가 미리 prepared artifact를 준비해야 하며, artifact preflight가 실패하면 `.env`를 수정하거나 `docker compose up`까지 가지 않고 중단한다.

`PREPARE_COLBERT_KO_ARTIFACT=1`은 target host의 Python이나 `huggingface_hub` 설치를 사용하지 않는다. Deploy script는 먼저 `PLATFORM_IMAGE_TO_DEPLOY`를 pull한 뒤 다음 형태로 platform image 컨테이너를 실행한다.

```bash
docker run --rm \
  -v "${COLBERT_KO_MODEL_DIR}:/out" \
  -v "${HF_CACHE_DIR_HOST}:/root/.cache/huggingface" \
  "${PLATFORM_IMAGE_TO_DEPLOY}" \
  python scripts/models/prepare_colbert_ko_vllm_artifact.py \
    --output-dir /out
```

따라서 target host에는 Docker, registry access, Hugging Face network access, `COLBERT_KO_MODEL_DIR` write permission, 그리고 writable Hugging Face cache directory가 필요하다. `COLBERT_KO_MODEL_DIR`는 절대경로여야 하고, raw HF cache를 `COLBERT_KO_MODEL_DIR`로 지정하면 안 된다.

`HF_CACHE_DIR`가 절대경로이면 deploy script는 그대로 사용한다. 상대경로이면 `DEPLOY_PATH` root가 아니라 compose file directory 기준으로 해석한다. 예:

```text
DEPLOY_PATH=/opt/acl-ai-gateway
COMPOSE_FILE=ops/compose/full-stack.private-network.yaml
HF_CACHE_DIR=./model_cache/huggingface
resolved path=/opt/acl-ai-gateway/ops/compose/model_cache/huggingface
```

이는 `PREPARE_COLBERT_KO_ARTIFACT=1`의 one-shot platform container와 이후 `docker compose` runtime이 같은 Hugging Face cache host path를 사용하게 하기 위한 정책이다. 로컬 make/compose 실행은 계속 CI/CD와 독립적이며, 이 path resolution은 production deploy script에만 적용된다.

## 로컬 빌드와 CI 빌드 분리

CI job과 로컬 make target은 목적이 다르며 독립적으로 실행된다.

| 목적 | CI job / 로컬 명령 | 변수 기반 |
|---|---|---|
| Platform 이미지 빌드 + push | `build-platform` (CI) | `CI_REGISTRY_IMAGE` 등 CI 변수 |
| risk/colbert 이미지 빌드 + push | `build-vllm-derived` (CI) | `VLLM_BASE_IMAGE`, `*_IMAGE_SHA` 등 CI 변수 |
| 로컬 risk vLLM 이미지 빌드 | `make build-risk-vllm-image` | `.env`의 `RISK_VLLM_BASE_IMAGE`, `RISK_VLLM_IMAGE` |
| 로컬 colbert-ko-vllm 이미지 빌드 | `make build-colbert-ko-vllm-image` | `.env`의 `COLBERT_KO_VLLM_BASE_IMAGE`, `VLLM_IMAGE` |

`VLLM_BASE_IMAGE`는 CI `build-vllm-derived`의 canonical base image 변수다. 로컬 ColBERT build는 CI 변수 없이 `COLBERT_KO_VLLM_BASE_IMAGE` 또는 `VLLM_IMAGE`로 독립 실행한다.

`make init-env-compose`로 만든 로컬 `.env`는 재현성을 위해 `COLBERT_KO_MODEL_DIR=./models/colbert-ko-vllm`를 사용한다. GitLab production full deploy만 절대경로를 강제한다.

공통으로 사용하는 것: `ops/docker/Dockerfile.risk-vllm-kanana`, `ops/docker/Dockerfile.colbert-ko-vllm`.

운영 원칙:

- CI/CD: release, registry push, deploy orchestration
- Local: CI 없이 build/test/compose 재현
- Shared: Dockerfile, compose validation, model contract

로컬 빌드 명령과 CI/릴리스 빌드 절차 전반은 [`docs/development/build_ux.md`](../development/build_ux.md)를 기준으로 한다.
