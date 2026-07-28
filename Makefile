SHELL := /usr/bin/env bash
PROJECT_NAME := ai_model_serving_platform
CURRENT_VERSION := $(shell cat VERSION 2>/dev/null || echo 0.0.0)

PYTHON ?= $(if $(PYTHON_BIN),$(PYTHON_BIN),$(shell command -v python3.12 || command -v python3 || command -v python))
export PYTHON_BIN := $(PYTHON)
AUTH_ENV ?= $(if $(ENV_FILE),$(ENV_FILE),$(ENV))
AUTH_ENV_ARG = $(if $(AUTH_ENV),--env $(AUTH_ENV),)


.PHONY: help help-full help-json command-check guide init-env init-env-local init-env-compose init-env-local-force init-env-compose-force sync-runtime-secrets sync-env show-image-tags validate test test-full build build-pipeline build-image build-vllm-unified-image rebuild-app rebuild-vllm-unified package start up compose-up compose-up-master compose-up-private compose-down-private preflight-compose compose-config ready ready-local ready-full check-ready smoke runtime-validate runtime-targets storage-paths project-inventory refresh-generated-reports auth-status auth-doctor auth-plan auth-apply exposure-status exposure-plan exposure-apply monitoring-projection operator-status operator-reports live-evidence release-check release-check-full vllm-commands hf-config-check main-model-prepare risk-vllm-config-check risk-vllm-patch-removal-check model-inventory model-list model-status model-validate model-diff model-propose-add model-propose-remove status stop down compose-down compose-restart compose-logs logs compose-diagnostics clean clean-dry-run cleanup-plan remove-plan clean-all reset bootstrap first-run rebuild-full doctor reset-version infisical-up infisical-down infisical-logs infisical-init secrets-push secrets-push-sensitive secrets-pull secrets-status validate-docs docs-check reports-check feature-check feature-plan render-runtime-assets check-runtime-assets

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

# build UX contract markers (validate_contracts.py exact-match 검사용; make help 출력 대상 아님)
# make build         # build artifacts/images only; does not start or keep services alive

init-env: init-env-compose

init-env-local:
	$(PYTHON) scripts/config/setup_env.py --profile local

init-env-compose:
	$(PYTHON) scripts/config/setup_env.py --profile compose

init-env-local-force:
	$(PYTHON) scripts/config/setup_env.py --profile local --force

init-env-compose-force:
	$(PYTHON) scripts/config/setup_env.py --profile compose --force

sync-runtime-secrets:
	$(PYTHON) scripts/config/setup_env.py --sync-runtime-secrets --env-file "$${ENV_FILE:-.env}"

sync-env:
	$(PYTHON) scripts/config/setup_env.py --sync-env --env-file "$${ENV_FILE:-.env}"

show-image-tags:
	$(PYTHON) scripts/config/setup_env.py --show-image-tags

validate:
	$(PYTHON) scripts/build/check_python.py --context validate >/dev/null
	$(PYTHON) scripts/validation/validate_contracts.py
	$(MAKE) validate-docs
	$(MAKE) command-check

test:
	PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PYTHON) scripts/validation/run_tests.py -q -m "not slow and not runtime and not docker and not gpu"

test-full:
	PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PYTHON) scripts/validation/run_tests.py -q -m "not runtime and not docker and not gpu"

build:
	bash scripts/build/build_all.sh

build-pipeline: build

build-image:
	bash scripts/build/build_platform_image.sh

rebuild-app: build-image

build-vllm-unified-image:
	bash scripts/build/build_vllm_unified_image.sh

rebuild-vllm-unified: build-vllm-unified-image


refresh-generated-reports:
	$(PYTHON) scripts/reports/refresh_generated_reports.py

package: refresh-generated-reports
	$(PYTHON) scripts/build/check_python.py --context package >/dev/null
	$(PYTHON) scripts/validation/validate_contracts.py
	PACKAGE_SKIP_VALIDATION=1 bash scripts/build/package_release.sh

start:
	bash scripts/ops/start_services.sh

up: start

compose-up:
	bash scripts/compose/compose_up.sh

compose-up-master:
	EXPOSURE_MODE=master_open bash scripts/compose/compose_up.sh

compose-up-private:
	@if [[ ! -f "$${ENV_FILE:-.env}" ]]; then echo "오류: $${ENV_FILE:-.env} 파일이 없습니다. make init-env-compose 를 먼저 실행하세요." >&2; exit 2; fi
	SKIP_PREFLIGHT=1 EXPOSURE_MODE=private_network bash scripts/compose/compose_up.sh

compose-down-private:
	COMPOSE_FILE="$${COMPOSE_FILE:-ops/compose/full-stack.private-network.yaml}" ENV_FILE="$${ENV_FILE:-.env}" bash scripts/ops/down_services.sh

compose-config:
	@bash scripts/compose/compose_config.sh

preflight-compose:
	bash scripts/compose/preflight_compose.sh

ready: ready-full

ready-local:
	bash scripts/ops/ready_local.sh

ready-full:
	bash scripts/ops/ready_full.sh

check-ready: ready-full

smoke:
	bash scripts/ops/smoke_test.sh

runtime-validate:
	$(PYTHON) scripts/validation/runtime_validation.py

runtime-targets:
	$(PYTHON) scripts/reports/runtime_targets_report.py

storage-paths:
	$(PYTHON) scripts/reports/storage_paths_report.py

project-inventory:
	$(PYTHON) scripts/reports/project_inventory_report.py

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

operator-reports: runtime-targets storage-paths project-inventory auth-status auth-doctor monitoring-projection operator-status live-evidence

live-evidence: operator-status
	$(PYTHON) scripts/reports/live_evidence_bundle.py

release-check:
	$(PYTHON) scripts/build/check_python.py --context validate >/dev/null
	$(PYTHON) scripts/validation/release_check.py

release-check-full:
	$(PYTHON) scripts/build/check_python.py --context validate >/dev/null
	$(PYTHON) scripts/validation/release_check.py --include-tests

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

model-inventory:
	$(PYTHON) scripts/models/model_inventory.py

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
	bash scripts/ops/stop_services.sh

down: stop

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

cleanup-plan: clean-dry-run

remove-plan: cleanup-plan

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

# ── 시크릿 관리 (Infisical) ──────────────────────────────────────────────
infisical-up:
	@if [[ ! -f .env ]]; then \
		echo "오류: .env 파일이 없습니다. make init-env-compose 를 먼저 실행하세요."; \
		exit 2; \
	fi
	docker compose -f ops/compose/infisical.yaml --env-file .env up -d
	@echo ""
	@echo "Infisical 웹 UI: http://localhost:$$(grep '^INFISICAL_PORT=' .env | cut -d= -f2 || echo 9420)"

infisical-down:
	docker compose -f ops/compose/infisical.yaml --env-file .env down

infisical-logs:
	docker compose -f ops/compose/infisical.yaml --env-file .env logs -f --tail=100

infisical-init:
	@echo "=== Infisical Machine Identity 설정 가이드 ==="
	@echo ""
	@echo "사전 조건: make infisical-up 이 완료된 상태"
	@echo ""
	@echo "1. http://localhost:9420 접속 → 관리자 계정 생성"
	@echo "2. Organization 생성 (예: ai-model-serving)"
	@echo "3. Project 생성 (예: platform) — staging 환경 포함"
	@echo "4. 좌측 사이드바 → Organization → Machine Identities → Create"
	@echo "   - Universal Auth 선택"
	@echo "   - Client ID, Client Secret 복사"
	@echo "5. Project → Access Control → Machine Identities → Add"
	@echo "   - 생성한 Machine Identity 추가 (Developer 이상 권한)"
	@echo "6. Project → Settings → Project ID 복사"
	@echo "7. .env 에 값 입력:"
	@echo "   INFISICAL_CLIENT_ID=<4번에서 복사>"
	@echo "   INFISICAL_CLIENT_SECRET=<4번에서 복사>"
	@echo "   INFISICAL_PROJECT_ID=<6번에서 복사>"
	@echo "8. make secrets-push 로 현재 .env 시크릿 동기화"

secrets-push:
	$(PYTHON) scripts/config/infisical_sync.py push

secrets-push-sensitive:
	$(PYTHON) scripts/config/infisical_sync.py push --secrets-only

secrets-pull:
	$(PYTHON) scripts/config/infisical_sync.py pull

secrets-status:
	$(PYTHON) scripts/config/infisical_sync.py status

validate-docs:
	$(PYTHON) scripts/validation/check_docs_links.py
	$(PYTHON) scripts/validation/check_reports.py --stale-only
	$(PYTHON) scripts/validation/check_features.py

docs-check:
	$(PYTHON) scripts/validation/check_docs_links.py

reports-check:
	$(PYTHON) scripts/validation/check_reports.py

feature-check:
	$(PYTHON) scripts/validation/check_features.py

feature-plan:
	@if [[ -z "$(ID)" ]]; then \
		$(PYTHON) scripts/reports/feature_plan.py --list; \
	else \
		$(PYTHON) scripts/reports/feature_plan.py --id $(ID); \
	fi

render-runtime-assets:
	$(PYTHON) scripts/render_runtime_assets.py --write

check-runtime-assets:
	@# 생성 artifact drift 검출 + compose vLLM command drift 검증 (exit 1 on drift)
	$(PYTHON) scripts/render_runtime_assets.py --check
	$(PYTHON) scripts/compose/validate_vllm_compose.py
