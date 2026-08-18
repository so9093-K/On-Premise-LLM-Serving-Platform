SHELL := /usr/bin/env bash
PROJECT_NAME := ai_model_serving_platform
CURRENT_VERSION := $(shell cat VERSION 2>/dev/null || echo 0.0.0)

# bootstrap이 만든 lock-file 기반 .venv가 있으면 로컬 Make 명령은 이를 우선한다.
# CI와 호출자가 PYTHON_BIN으로 지정한 interpreter는 항상 그보다 우선한다.
PYTHON ?= $(if $(PYTHON_BIN),$(PYTHON_BIN),$(if $(wildcard $(CURDIR)/.venv/bin/python),$(CURDIR)/.venv/bin/python,$(shell command -v python3.12 || command -v python3 || command -v python)))
export PYTHON_BIN := $(PYTHON)
AUTH_ENV ?= $(if $(ENV_FILE),$(ENV_FILE),$(ENV))
AUTH_ENV_ARG = $(if $(AUTH_ENV),--env $(AUTH_ENV),)


.PHONY: help init-env-local init-env-compose init-env-compose-force sync-runtime-secrets sync-env validate test build build-image build-vllm-unified-image package start compose-up preflight-compose compose-config ready-local ready-full smoke runtime-validate auth-status auth-doctor auth-plan auth-apply exposure-status exposure-plan exposure-apply main-model-prepare risk-vllm-config-check status stop compose-down compose-restart compose-logs logs compose-diagnostics clean clean-dry-run remove-plan clean-all reset first-run doctor reset-version render-runtime-assets

help:
	@echo "ai_model_serving_platform $(CURRENT_VERSION)"
	@echo ""
	@echo "  make validate             # 정적 계약·설정·생성물 drift 검증"
	@echo "  make test                 # 결정론적 unit·contract 테스트"
	@echo "  make build                # validate + test + platform image build"
	@echo "  make package              # 릴리스 ZIP 생성"
	@echo "  make init-env-local       # 로컬 app-only .env 생성"
	@echo "  make start                # Gateway·Risk Adapter 기동"
	@echo "  make ready-local          # app-only readiness"
	@echo "  make compose-up           # GPU full-stack compose 기동"
	@echo "  make ready-full           # vLLM 포함 readiness"
	@echo "  make runtime-validate     # 실제 서비스·GPU 검증"
	@echo "  make status               # 서비스 상태"
	@echo ""
	@echo "상세 운영 문서: docs/README.md"

init-env-local:
	$(PYTHON) scripts/config/setup_env.py --profile local

init-env-compose:
	$(PYTHON) scripts/config/setup_env.py --profile compose

init-env-compose-force:
	$(PYTHON) scripts/config/setup_env.py --profile compose --force

sync-runtime-secrets:
	$(PYTHON) scripts/config/setup_env.py --sync-runtime-secrets --env-file "$${ENV_FILE:-.env}"

sync-env:
	$(PYTHON) scripts/config/setup_env.py --sync-env --env-file "$${ENV_FILE:-.env}"

validate:
	PYTHON_BIN="$(PYTHON)" bash scripts/validation/run_validate.sh

test:
	PYTHON_BIN="$(PYTHON)" bash scripts/validation/run_test.sh

build:
	bash scripts/build/build_all.sh

build-image:
	bash scripts/build/build_platform_image.sh

build-vllm-unified-image:
	bash scripts/build/build_vllm_unified_image.sh

package:
	bash scripts/build/package_release.sh

start:
	bash scripts/ops/up_services.sh

compose-up:
	bash scripts/compose/compose_up.sh

compose-config:
	@bash scripts/compose/compose_config.sh

preflight-compose:
	bash scripts/compose/preflight_compose.sh

ready-local:
	bash scripts/ops/ready_local.sh

ready-full:
	bash scripts/ops/ready_full.sh

smoke:
	bash scripts/ops/smoke_test.sh

runtime-validate:
	$(PYTHON) scripts/validation/runtime_validation.py

auth-status:
	$(PYTHON) scripts/auth/auth_status.py $(AUTH_ENV_ARG)

auth-doctor:
	$(PYTHON) scripts/auth/auth_doctor.py $(AUTH_ENV_ARG) --warn-only

auth-plan:
	@if [[ -z "$(MODE)" ]]; then echo "MODE=local_open|internal_trusted|private_network|edge_terminated|strict 를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/auth/auth_plan.py $(AUTH_ENV_ARG) --mode $(MODE)

auth-apply:
	@if [[ -z "$(MODE)" ]]; then echo "MODE=local_open|internal_trusted|private_network|edge_terminated|strict 를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/auth/auth_apply.py $(AUTH_ENV_ARG) --mode $(MODE) --yes

exposure-status:
	$(PYTHON) scripts/auth/exposure_status.py $(AUTH_ENV_ARG)

exposure-plan:
	@if [[ -z "$(MODE)" ]]; then echo "MODE=private_network|master_open 를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/auth/exposure_plan.py $(AUTH_ENV_ARG) --mode $(MODE) $(if $(AUDIENCE),--audience $(AUDIENCE),)

exposure-apply:
	@if [[ -z "$(MODE)" ]]; then echo "MODE=private_network|master_open 를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/auth/exposure_apply.py $(AUTH_ENV_ARG) --mode $(MODE) $(if $(AUDIENCE),--audience $(AUDIENCE),) --yes

main-model-prepare:
	@if [[ -z "$(PROFILE)" ]]; then echo "PROFILE=<main-model-profile-id>를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/models/prepare_main_model_cache.py --profile "$(PROFILE)" --env-file "$${ENV_FILE:-.env}" --compose-file "$${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"

risk-vllm-config-check:
	bash scripts/models/check_risk_vllm_image_config.sh

status:
	@if [[ "$(READY_MODE)" == "full" ]]; then bash scripts/ops/status_services.sh --full; else bash scripts/ops/status_services.sh --local; fi

stop:
	bash scripts/ops/down_services.sh

compose-down:
	bash scripts/ops/down_services.sh

compose-restart:
	bash scripts/compose/compose_restart.sh

compose-logs:
	bash scripts/compose/compose_logs.sh

compose-diagnostics:
	bash scripts/compose/compose_diagnostics.sh

logs:
	@mkdir -p logs
	@tail -n 100 -f logs/gateway.log logs/risk_adapter.log

clean:
	bash scripts/ops/clean_all.sh

clean-dry-run:
	bash scripts/ops/clean_all.sh --dry-run

remove-plan: clean-dry-run

clean-all:
	bash scripts/ops/clean_all.sh --all

reset:
	bash scripts/ops/reset_all.sh

first-run:
	bash scripts/build/bootstrap.sh

doctor:
	bash scripts/ops/doctor.sh

reset-version:
	@if [[ -z "$(NEW_VERSION)" ]]; then echo "Usage: make reset-version NEW_VERSION=0.1.0"; exit 2; fi
	$(PYTHON) scripts/build/reset_version.py "$(NEW_VERSION)"
	$(MAKE) validate

render-runtime-assets:
	$(PYTHON) scripts/render_runtime_assets.py --write
