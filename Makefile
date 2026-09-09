SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help
PROJECT_NAME := ai_model_serving_platform
CURRENT_VERSION := $(shell cat VERSION 2>/dev/null || echo 0.0.0)

# bootstrap이 만든 uv.lock 기반 .venv가 있으면 로컬 Make 명령은 이를 우선한다.
# CI와 호출자가 PYTHON_BIN으로 지정한 interpreter는 항상 그보다 우선한다.
PYTHON ?= $(if $(PYTHON_BIN),$(PYTHON_BIN),$(if $(wildcard $(CURDIR)/.venv/bin/python),$(CURDIR)/.venv/bin/python,$(shell command -v python3.13 || command -v python3.12 || command -v python3 || command -v python)))
export PYTHON_BIN := $(PYTHON)
UV ?= uv
export UV_BIN := $(UV)
AUTH_ENV ?= $(if $(ENV_FILE),$(ENV_FILE),$(ENV))
AUTH_ENV_ARG = $(if $(AUTH_ENV),--env $(AUTH_ENV),)


.PHONY: help help-all setup build rebuild prepare up status down down-all check init-env-local init-env-compose sync-env static-compose-config metal-doctor metal-command metal-start validate test build-image build-vllm-unified-image lock package compose-up compose-config ready-local ready-full smoke runtime-validate auth-status auth-doctor auth-plan auth-apply exposure-status exposure-plan exposure-apply main-model-prepare compose-down compose-restart compose-logs logs compose-diagnostics clean reset reset-version render-runtime-assets
.PHONY: setup-dev doctor-dev

PUBLIC_TARGETS := setup build prepare up status down
RECOVERY_TARGETS := rebuild down-all reset
QUALITY_TARGETS := check
PLATFORM_CLI := "$(CURDIR)/.venv/bin/python" scripts/platform_cli.py
PLATFORM_TARGET_ARG = $(if $(TARGET),--target "$(TARGET)",)
PLATFORM_PROFILE_ARG = $(if $(MODEL),--main-profile "$(MODEL)",)
PLATFORM_MAIN_URL_ARG = $(if $(MAIN_URL),--main-base-url "$(MAIN_URL)",)

setup: ## 선택 target의 로컬 환경과 설정을 한 번 준비 (TARGET=<id>, MODEL=<profile>)
	"$(PYTHON)" scripts/build/setup_dev.py
	$(PLATFORM_CLI) setup $(PLATFORM_TARGET_ARG) $(PLATFORM_PROFILE_ARG) $(PLATFORM_MAIN_URL_ARG)

build: ## 선택 target에서 이 저장소가 소유한 image 전체 빌드
	$(PLATFORM_CLI) build $(PLATFORM_TARGET_ARG)

rebuild: ## 선택 target의 project-owned image를 cache 재사용 없이 다시 빌드
	$(PLATFORM_CLI) rebuild $(PLATFORM_TARGET_ARG)

prepare: ## 선택 target의 선택 Main model 준비 (secondary model 제외)
	$(PLATFORM_CLI) prepare $(PLATFORM_TARGET_ARG)

up: ## .env에 선택된 target 전체 기동 후 readiness 확인
	$(PLATFORM_CLI) up $(PLATFORM_TARGET_ARG)

down: ## .env에 선택된 target 전체 정지
	$(PLATFORM_CLI) down $(PLATFORM_TARGET_ARG)

down-all: ## .env와 무관하게 이 checkout이 소유한 모든 실행 리소스 정지
	bash scripts/ops/down_all.sh

reset: ## 프로젝트 로컬 상태 초기화 계획 출력 (적용: CONFIRM=reset)
	bash scripts/ops/reset_all.sh $(if $(filter reset,$(CONFIRM)),--confirm reset,)

check: ## application 변경의 정적 계약과 결정론적 테스트 확인
	$(MAKE) validate
	$(MAKE) test

setup-dev: ## Platform 개발용 .venv 준비 (Docker·GPU·.env 불필요)
	"$(PYTHON)" scripts/build/setup_dev.py

doctor-dev: ## Python과 운영 스크립트용 Bash 확인
	"$(PYTHON)" scripts/build/check_dev_environment.py

help:
	@echo "ai_model_serving_platform $(CURRENT_VERSION)"
	@echo ""
	@echo "로컬 lifecycle"
	@for target in $(PUBLIC_TARGETS); do \
		awk -v wanted="$$target" 'BEGIN {FS = ":.*?## "} $$1 == wanted {printf "  make %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST); \
	done
	@echo ""
	@echo "처음 한 번: make setup TARGET=<deployment-target>"
	@echo "기본 순서: setup → build → prepare → up → status/down"
	@echo ""
	@echo "복구·초기화"
	@for target in $(RECOVERY_TARGETS); do \
		awk -v wanted="$$target" 'BEGIN {FS = ":.*?## "} $$1 == wanted {printf "  make %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST); \
	done
	@echo ""
	@echo "변경 검증"
	@for target in $(QUALITY_TARGETS); do \
		awk -v wanted="$$target" 'BEGIN {FS = ":.*?## "} $$1 == wanted {printf "  make %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST); \
	done
	@echo ""
	@echo "고급·유지보수 명령: make help-all"

help-all: ## 내부 단계와 운영 진단을 포함한 전체 명령
	@echo "ai_model_serving_platform $(CURRENT_VERSION) — all commands"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  make %-26s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

init-env-local: ## 로컬 app-only .env 생성
	$(PYTHON) scripts/config/setup_env.py --profile local

init-env-compose: ## compose용 .env 생성 (기존 .env가 있으면 실패)
	$(PYTHON) scripts/config/setup_env.py --profile compose

sync-env: ## template에 추가된 새 키를 .env에 동기화 (기존 값 보존)
	$(PYTHON) scripts/config/setup_env.py --sync-env --env-file "$(if $(ENV_FILE),$(ENV_FILE),.env)"

static-compose-config: ## static Gateway의 분리된 Compose 정의 출력
	bash scripts/compose/static_main_compose.sh config

metal-doctor: ## Apple Silicon과 고정 MLX runtime 설정 확인
	$(PYTHON) scripts/runtime/macos_mlx_runtime.py doctor

metal-command: ## cache-resolved MLX server 실행 명령 출력
	$(PYTHON) scripts/runtime/macos_mlx_runtime.py command $(if $(METAL_LISTEN_HOST),--listen-host $(METAL_LISTEN_HOST),)

metal-start: ## cache된 모델로 MLX server foreground 기동 (암묵적 다운로드 없음)
	$(PYTHON) scripts/runtime/macos_mlx_runtime.py start $(if $(METAL_LISTEN_HOST),--listen-host $(METAL_LISTEN_HOST),)

validate: ## 정적 계약·설정·생성물 drift 검증
	@PYTHON_BIN="$(PYTHON)" bash scripts/validation/run_validate.sh

test: ## 결정론적 unit·contract 테스트
	@PYTHON_BIN="$(PYTHON)" bash scripts/validation/run_test.sh

build-image: ## 로컬 Docker platform image build (daemon 기본 architecture)
	bash scripts/build/build_platform_image.sh

build-vllm-unified-image: ## native linux/amd64 Docker의 NVIDIA vLLM image build
	bash scripts/build/build_vllm_unified_image.sh

lock: ## Platform과 MLX dependency lock 갱신 (암묵적 전체 upgrade 없음)
	$(UV) lock --python "$(PYTHON)"
	$(UV) lock --project runtimes/mlx --python "$(PYTHON)"

package: ## 릴리스 ZIP 생성
	bash scripts/build/package_release.sh

compose-up: ## GPU full-stack compose 기동
	bash scripts/compose/compose_up.sh

compose-config: ## resolve된 compose 정의 출력
	@bash scripts/compose/compose_config.sh

ready-local: ## app-only readiness
	bash scripts/ops/ready_local.sh

ready-full: ## vLLM 포함 readiness
	bash scripts/ops/ready_full.sh

smoke: ## smoke test 실행
	bash scripts/ops/smoke_test.sh

runtime-validate: ## 실제 서비스·GPU 검증
	$(PYTHON) scripts/validation/runtime_validation.py

auth-status: ## 현재 public/admin/internal 인증 상태
	$(PYTHON) scripts/auth/auth_status.py $(AUTH_ENV_ARG)

auth-doctor: ## 위험한 인증 조합 탐지
	$(PYTHON) scripts/auth/auth_doctor.py $(AUTH_ENV_ARG) --warn-only

auth-plan: ## MODE=<mode> 인증 프로필 변경 계획 (secret 미출력)
	@if [[ -z "$(MODE)" ]]; then echo "MODE=local_open|internal_trusted|private_network|edge_terminated|strict 를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/auth/auth_plan.py $(AUTH_ENV_ARG) --mode $(MODE)

auth-apply: ## MODE=<mode> managed 인증 flag 적용
	@if [[ -z "$(MODE)" ]]; then echo "MODE=local_open|internal_trusted|private_network|edge_terminated|strict 를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/auth/auth_apply.py $(AUTH_ENV_ARG) --mode $(MODE) --yes

exposure-status: ## 현재 노출(exposure) 상태
	$(PYTHON) scripts/auth/exposure_status.py $(AUTH_ENV_ARG)

exposure-plan: ## MODE=<mode> 노출 변경 계획
	@if [[ -z "$(MODE)" ]]; then echo "MODE=private_network|master_open 를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/auth/exposure_plan.py $(AUTH_ENV_ARG) --mode $(MODE) $(if $(AUDIENCE),--audience $(AUDIENCE),)

exposure-apply: ## MODE=<mode> 노출 설정 적용
	@if [[ -z "$(MODE)" ]]; then echo "MODE=private_network|master_open 를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/auth/exposure_apply.py $(AUTH_ENV_ARG) --mode $(MODE) $(if $(AUDIENCE),--audience $(AUDIENCE),) --yes

main-model-prepare: ## PROFILE=<id> main-model 캐시 준비 (런타임 미변경)
	@if [[ -z "$(PROFILE)" ]]; then echo "PROFILE=<main-model-profile-id>를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/models/prepare_main_model_cache.py --profile "$(PROFILE)" --env-file "$${ENV_FILE:-.env}" --compose-file "$${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"

status: ## .env에 선택된 target의 runtime·서비스 상태 확인
	$(PLATFORM_CLI) status $(PLATFORM_TARGET_ARG)

compose-down: ## compose 스택 정지
	bash scripts/ops/down_services.sh --compose

compose-restart: ## compose 스택 재시작
	bash scripts/compose/compose_restart.sh

compose-logs: ## compose 로그
	bash scripts/compose/compose_logs.sh

compose-diagnostics: ## ready-full 실패 시 상태·로그 수집
	bash scripts/compose/compose_diagnostics.sh

logs: ## 로컬 app 로그 tail (app-only make up 이후)
	@if ! ls logs/*.log >/dev/null 2>&1; then \
		echo "logs/ 에 로그 파일이 없습니다. app-only 환경에서 'make up'을 먼저 실행하세요." >&2; \
		exit 2; \
	fi
	@tail -n 100 -f logs/*.log

clean: ## 저비용 산출물 정리 (DRY_RUN=1, LOGS=1)
	bash scripts/ops/clean_project.sh $(if $(filter 1,$(DRY_RUN)),--dry-run,) $(if $(filter 1,$(LOGS)),--logs,)

reset-version: ## NEW_VERSION=<x.y.z> 버전을 선언된 모든 자리에 반영
	@if [[ -z "$(NEW_VERSION)" ]]; then echo "Usage: make reset-version NEW_VERSION=0.1.0"; exit 2; fi
	$(PYTHON) scripts/build/reset_version.py "$(NEW_VERSION)"
	$(MAKE) validate

render-runtime-assets: ## 생성 runtime asset 다시 렌더링
	$(PYTHON) scripts/render_runtime_assets.py --write
