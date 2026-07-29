SHELL := /usr/bin/env bash
PROJECT_NAME := ai_model_serving_platform
CURRENT_VERSION := $(shell cat VERSION 2>/dev/null || echo 0.0.0)

# bootstrap이 만든 lock-file 기반 .venv가 있으면 로컬 Make 명령은 이를 우선한다.
# CI와 호출자가 PYTHON_BIN으로 지정한 interpreter는 항상 그보다 우선한다.
PYTHON ?= $(if $(PYTHON_BIN),$(PYTHON_BIN),$(if $(wildcard $(CURDIR)/.venv/bin/python),$(CURDIR)/.venv/bin/python,$(shell command -v python3.12 || command -v python3 || command -v python)))
export PYTHON_BIN := $(PYTHON)
AUTH_ENV ?= $(if $(ENV_FILE),$(ENV_FILE),$(ENV))
AUTH_ENV_ARG = $(if $(AUTH_ENV),--env $(AUTH_ENV),)


.PHONY: help help-full help-json command-check guide init-env-local init-env-compose init-env-compose-force sync-runtime-secrets sync-env validate test build build-image build-vllm-unified-image rebuild-app rebuild-vllm-unified package start compose-up compose-up-master compose-up-private preflight-compose compose-config ready-local ready-full smoke runtime-validate runtime-targets auth-status auth-doctor auth-plan auth-apply exposure-status exposure-plan exposure-apply monitoring-projection operator-status operator-reports live-evidence vllm-commands hf-config-check main-model-prepare risk-vllm-config-check risk-vllm-patch-removal-check model-list model-status model-validate model-diff model-propose-add model-propose-remove status stop compose-down compose-restart compose-logs logs compose-diagnostics clean clean-dry-run remove-plan clean-all reset bootstrap first-run rebuild-full doctor reset-version render-runtime-assets check-runtime-assets

help:
	@$(PYTHON) scripts/commands/render_command_help.py

help-full:
	@$(PYTHON) scripts/commands/render_command_help.py --mode full

help-json:
	@$(PYTHON) scripts/commands/render_command_help.py --json

command-check:
	$(PYTHON) scripts/commands/validate_command_registry.py --strict

guide:
	$(PYTHON) scripts/reports/operator_guide.py

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

rebuild-app: build-image

build-vllm-unified-image:
	bash scripts/build/build_vllm_unified_image.sh

rebuild-vllm-unified: build-vllm-unified-image


package:
	bash scripts/build/package_release.sh

start:
	bash scripts/ops/up_services.sh

compose-up:
	bash scripts/compose/compose_up.sh

compose-up-master:
	EXPOSURE_MODE=master_open bash scripts/compose/compose_up.sh

compose-up-private:
	@if [[ ! -f "$${ENV_FILE:-.env}" ]]; then echo "오류: $${ENV_FILE:-.env} 파일이 없습니다. make init-env-compose 를 먼저 실행하세요." >&2; exit 2; fi
	SKIP_PREFLIGHT=1 EXPOSURE_MODE=private_network bash scripts/compose/compose_up.sh

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

runtime-targets:
	$(PYTHON) scripts/reports/runtime_targets_report.py

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

monitoring-projection:
	$(PYTHON) scripts/reports/monitoring_projection_report.py

operator-status:
	$(PYTHON) scripts/reports/operator_status_bundle.py

operator-reports: runtime-targets auth-status auth-doctor monitoring-projection operator-status live-evidence

live-evidence: operator-status
	$(PYTHON) scripts/reports/live_evidence_bundle.py

vllm-commands:
	$(PYTHON) scripts/models/render_vllm_commands.py

hf-config-check:
	$(PYTHON) scripts/models/check_hf_model_config.py

main-model-prepare:
	@if [[ -z "$(PROFILE)" ]]; then echo "PROFILE=<main-model-profile-id>를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/models/prepare_main_model_cache.py --profile "$(PROFILE)" --env-file "$${ENV_FILE:-.env}" --compose-file "$${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}"

risk-vllm-config-check:
	bash scripts/models/check_risk_vllm_image_config.sh

risk-vllm-patch-removal-check:
	bash scripts/models/risk_vllm_patch_removal_check.sh

model-list:
	$(PYTHON) scripts/models/modelctl.py list

model-status:
	$(PYTHON) scripts/models/modelctl.py status

model-validate:
	$(PYTHON) scripts/models/modelctl.py validate

model-diff:
	$(PYTHON) scripts/models/modelctl.py diff

model-propose-add:
	@if [[ -z "$(ID)" || -z "$(PORT)" || -z "$(ENDPOINT)" || -z "$(UPSTREAM)" || -z "$(ROLE)" ]]; then echo "ID, PORT, ENDPOINT, UPSTREAM, ROLE을 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/models/modelctl.py propose-add --id $(ID) --role $(ROLE) --upstream-model-id $(UPSTREAM) --port $(PORT) --endpoint $(ENDPOINT) $(if $(CAPABILITIES),--capabilities $(CAPABILITIES),) $(if $(GPU),--gpu-memory-utilization $(GPU),) $(if $(WRITE_PLAN),--write-plan,) $(if $(WRITE_PATCH),--write-patch,) $(if $(OUTPUT_DIR),--output-dir $(OUTPUT_DIR),)

model-propose-remove:
	@if [[ -z "$(ID)" ]]; then echo "ID=<model-id>를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/models/modelctl.py propose-remove $(ID) $(if $(WRITE_PLAN),--write-plan,) $(if $(WRITE_PATCH),--write-patch,) $(if $(OUTPUT_DIR),--output-dir $(OUTPUT_DIR),)

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

bootstrap:
	bash scripts/build/bootstrap.sh

first-run: bootstrap

rebuild-full: bootstrap

doctor:
	bash scripts/ops/doctor.sh

reset-version:
	@if [[ -z "$(NEW_VERSION)" ]]; then echo "Usage: make reset-version NEW_VERSION=0.1.0"; exit 2; fi
	$(PYTHON) scripts/build/reset_version.py "$(NEW_VERSION)"

render-runtime-assets:
	$(PYTHON) scripts/render_runtime_assets.py --write

check-runtime-assets:
	@# 생성 artifact drift 검출 + compose vLLM command drift 검증 (exit 1 on drift)
	$(PYTHON) scripts/render_runtime_assets.py --check
	$(PYTHON) scripts/compose/validate_vllm_compose.py
