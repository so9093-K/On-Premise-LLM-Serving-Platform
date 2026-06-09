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

risk-vllm-kanana를 빌드한다. `embedding-ko-vllm`은 derived Dockerfile 빌드 대상이 아니라 `EMBEDDING_KO_VLLM_IMAGE`로 지정한 표준 vLLM 이미지를 사용한다. 실행된 job이 실패하면 release 실패로 처리된다(`allow_failure: false`). 빌드 로직은 `scripts/ci/build_vllm_derived_images.sh`에서 관리한다.

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
- `RUN_READY_FULL_SMOKE`: `1` 또는 `0`, 기본값 `0`. `DEPLOY_MODE=full`일 때 `make ready-full`을 추가 실행해 vLLM downstream readiness와 smoke를 함께 확인한다.
- `PRUNE_DANGLING_IMAGES`: `1` 또는 `0`, 기본값 `1`. 성공한 배포 뒤 태그가 사라진 dangling image만 정리
- `RISK_VLLM_IMAGE_TO_DEPLOY`: `DEPLOY_MODE=full`에서만 사용. risk vLLM image override가 필요할 때 175 `.env`의 `RISK_VLLM_IMAGE`를 해당 값으로 덮어쓴다

175의 `.env`에는 shared/staging 환경 기준으로 `GATEWAY_BIND_ADDR=<175 내부 IP>`를 명시하는 편이 안전하다. 전체 interface publish가 의도된 경우에만 `GATEWAY_BIND_ADDR=0.0.0.0`을 사용하고 firewall/network policy로 내부 CIDR만 허용한다. deploy smoke는 `GATEWAY_HEALTH_URL`이 없으면 175 `.env`의 `GATEWAY_BIND_ADDR`와 `GATEWAY_PORT`로 health URL을 만든다. `GATEWAY_BIND_ADDR=0.0.0.0`일 때만 `localhost`로 fallback한다. `RUN_READY_SMOKE=1`은 Gateway `/health`만 확인하므로, full runtime deploy에서 downstream vLLM까지 함께 gate하려면 `RUN_READY_FULL_SMOKE=1`을 사용한다.

배포 스크립트는 health check가 통과한 뒤 기본적으로 `docker image prune -f --filter dangling=true`를 실행한다. `release`처럼 같은 태그를 새 이미지가 덮어쓰면 이전 이미지가 `<none>` 상태로 남을 수 있는데, 이 단계는 실행 중인 컨테이너가 참조하지 않는 untagged image만 제거한다. 장애 분석이나 수동 롤백 때문에 보존이 필요하면 `PRUNE_DANGLING_IMAGES=0`으로 끈다.

## 배포 흐름

### Rolling app deploy (일반)

platform app 변경만 반영할 때 사용한다. vLLM-derived image는 재빌드하지 않는다.

1. `release` branch에 push → pipeline 자동 시작 (pipeline 변수 추가 없음)
2. `build-vllm-derived` 스킵 — `BUILD_VLLM_DERIVED` / `DEPLOY_MODE=full` 없으므로 조건 불충족
3. `deploy-gpu-175` 수동 실행 (기본 `DEPLOY_MODE=rolling`)
4. 175에서 `gateway`, `risk-adapter` image만 pull 후 재시작
5. vLLM 컨테이너는 건드리지 않으므로 GPU 모델 reload downtime 없음

### Build vLLM-derived images only

full deploy 없이 registry image만 미리 만들 때 사용한다. deploy mode를 바꾸지 않는다.

1. `release` branch 또는 tag pipeline을 `BUILD_VLLM_DERIVED=1`로 시작
2. `build-vllm-derived` 자동 실행
3. risk-vllm-kanana를 build/push. `embedding-ko-vllm`은 표준 vLLM 이미지(`EMBEDDING_KO_VLLM_IMAGE`)를 사용하므로 별도 build 없음
4. deploy mode는 바뀌지 않는다
5. full deploy까지 하려면 `DEPLOY_MODE=full`을 사용해야 한다

### Full runtime deploy (vLLM 이미지 갱신 포함)

risk-vllm-kanana를 교체하거나 `EMBEDDING_KO_VLLM_IMAGE`(표준 vLLM 이미지)를 갱신할 때 사용한다.

1. pipeline을 `DEPLOY_MODE=full` 변수로 시작
3. `deploy-gpu-175` 수동 실행, `DEPLOY_MODE=full` 설정
   - preflight 실패 시 `.env`를 수정하지 않고 실패; `build-vllm-derived` 먼저 실행하라는 안내 출력
5. 전체 stack `up -d --remove-orphans`; vLLM 이미지 pull과 모델 로딩 시간이 길 수 있다
6. 필요 시 `RUN_READY_FULL_SMOKE=1`로 `make ready-full`을 추가 실행해 Gateway `/ready`, downstream vLLM readiness, smoke를 함께 확인한다. 실패 시 compose diagnostics를 함께 출력한다.

## 배포 모드

`DEPLOY_MODE=rolling`은 app layer 배포다.

- `gateway`, `risk-adapter` image만 pull한다.
- `gateway`, `risk-adapter`만 `up -d --no-deps`로 재시작한다.
- vLLM 모델 컨테이너를 건드리지 않아 GPU 모델 reload downtime을 피한다.

`DEPLOY_MODE=full`은 초기 구축 또는 stack drift 정렬용이다.

- compose 전체 image를 pull한다.
- 전체 stack을 `up -d --remove-orphans`로 정렬한다.
- vLLM image pull과 모델 로딩 시간이 길 수 있다.

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



derived Dockerfile: `ops/docker/Dockerfile.risk-vllm-kanana`(risk-vllm-kanana 전용). `embedding-ko-vllm`은 표준 vLLM 이미지를 사용하며 derived Dockerfile이 없다.

운영 원칙:

- CI/CD: release, registry push, deploy orchestration
- Local: CI 없이 build/test/compose 재현
- Shared: Dockerfile, compose validation, model contract

로컬 빌드 명령과 CI/릴리스 빌드 절차 전반은 [`docs/development/build_ux.md`](../development/build_ux.md)를 기준으로 한다.
