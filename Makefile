SHELL := /usr/bin/env bash
PROJECT_NAME := ai_model_serving_platform
CURRENT_VERSION := $(shell cat VERSION 2>/dev/null || echo 0.0.0)

# bootstrap이 만든 lock-file 기반 .venv가 있으면 로컬 Make 명령은 이를 우선한다.
# CI와 호출자가 PYTHON_BIN으로 지정한 interpreter는 항상 그보다 우선한다.
PYTHON ?= $(if $(PYTHON_BIN),$(PYTHON_BIN),$(if $(wildcard $(CURDIR)/.venv/bin/python),$(CURDIR)/.venv/bin/python,$(shell command -v python3.12 || command -v python3 || command -v python)))
export PYTHON_BIN := $(PYTHON)
AUTH_ENV ?= $(if $(ENV_FILE),$(ENV_FILE),$(ENV))
AUTH_ENV_ARG = $(if $(AUTH_ENV),--env $(AUTH_ENV),)


.PHONY: help init-env-local init-env-compose sync-env static-compose-config static-compose-up static-compose-down validate test build build-image build-vllm-unified-image package start compose-up compose-config ready-local ready-full smoke runtime-validate auth-status auth-doctor auth-plan auth-apply exposure-status exposure-plan exposure-apply main-model-prepare status stop compose-down compose-restart compose-logs logs compose-diagnostics clean clean-dry-run clean-all reset first-run reset-version render-runtime-assets

# help는 각 타겟 옆의 `## 설명`을 읽는다. 예전에는 여기에 목록을 따로 적어뒀는데,
# 타겟이 늘어도 아무도 갱신하지 않아 44개 중 11개만 보이는 상태로 갈라져 있었다.
help:
	@echo "ai_model_serving_platform $(CURRENT_VERSION)"
	@echo ""
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  make %-26s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "상세 운영 문서: docs/README.md"

init-env-local: ## 로컬 app-only .env 생성
	$(PYTHON) scripts/config/setup_env.py --profile local

init-env-compose: ## compose용 .env 생성 (기존 .env가 있으면 실패)
	$(PYTHON) scripts/config/setup_env.py --profile compose

sync-env: ## template에 추가된 새 키를 .env에 동기화 (기존 값 보존)
	$(PYTHON) scripts/config/setup_env.py --sync-env --env-file "$(if $(ENV_FILE),$(ENV_FILE),.env)"

static-compose-config: ## static Gateway의 분리된 Compose 정의 출력
	bash scripts/compose/static_main_compose.sh config

static-compose-up: ## 외부 Main runtime에 연결하는 static Gateway 기동
	bash scripts/compose/static_main_compose.sh up -d

static-compose-down: ## static Gateway Compose project 정지
	bash scripts/compose/static_main_compose.sh down

validate: ## 정적 계약·설정·생성물 drift 검증
	PYTHON_BIN="$(PYTHON)" bash scripts/validation/run_validate.sh

test: ## 결정론적 unit·contract 테스트
	PYTHON_BIN="$(PYTHON)" bash scripts/validation/run_test.sh

build: ## validate + test + platform image build
	bash scripts/build/build_all.sh

build-image: ## platform image만 build
	bash scripts/build/build_platform_image.sh

build-vllm-unified-image: ## 모든 모델이 공유하는 vLLM unified image build
	bash scripts/build/build_vllm_unified_image.sh

package: ## 릴리스 ZIP 생성
	bash scripts/build/package_release.sh

start: ## Gateway·Risk Adapter 기동 (vLLM 없음)
	bash scripts/ops/up_services.sh

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

status: ## 서비스 상태 (READY_MODE=full이면 full-stack)
	@if [[ "$(READY_MODE)" == "full" ]]; then bash scripts/ops/status_services.sh --full; else bash scripts/ops/status_services.sh --local; fi

stop: ## app-only Gateway·Risk Adapter 정지
	bash scripts/ops/down_services.sh --local

compose-down: ## compose 스택 정지
	bash scripts/ops/down_services.sh --compose

compose-restart: ## compose 스택 재시작
	bash scripts/compose/compose_restart.sh

compose-logs: ## compose 로그
	bash scripts/compose/compose_logs.sh

compose-diagnostics: ## ready-full 실패 시 상태·로그 수집
	bash scripts/compose/compose_diagnostics.sh

logs: ## 로컬 app 로그 tail (make start 이후)
	@if ! ls logs/*.log >/dev/null 2>&1; then \
		echo "logs/ 에 로그 파일이 없습니다. 'make start'로 로컬 app을 먼저 기동하세요." >&2; \
		exit 2; \
	fi
	@tail -n 100 -f logs/*.log

clean: ## build 산출물·egg-info·로그 정리
	bash scripts/ops/clean_all.sh

clean-dry-run: ## clean 삭제 대상 미리보기
	bash scripts/ops/clean_all.sh --dry-run

clean-all: ## clean + 부가 산출물까지 정리
	bash scripts/ops/clean_all.sh --all

reset: ## 로컬 상태 초기화
	bash scripts/ops/reset_all.sh

first-run: ## bootstrap: venv·.env·image·검증 일괄 실행
	bash scripts/build/bootstrap.sh

reset-version: ## NEW_VERSION=<x.y.z> 버전을 선언된 모든 자리에 반영
	@if [[ -z "$(NEW_VERSION)" ]]; then echo "Usage: make reset-version NEW_VERSION=0.1.0"; exit 2; fi
	$(PYTHON) scripts/build/reset_version.py "$(NEW_VERSION)"
	$(MAKE) validate

render-runtime-assets: ## 생성 runtime asset 다시 렌더링
	$(PYTHON) scripts/render_runtime_assets.py --write
