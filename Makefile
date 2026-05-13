SHELL := /usr/bin/env bash
PROJECT_NAME := ai_model_serving_platform
CURRENT_VERSION := $(shell cat VERSION 2>/dev/null || echo 0.0.0)

PYTHON ?= $(if $(PYTHON_BIN),$(PYTHON_BIN),$(shell command -v python3.12 || command -v python3 || command -v python))
export PYTHON_BIN := $(PYTHON)
AUTH_ENV ?= $(ENV)
AUTH_ENV_ARG = $(if $(AUTH_ENV),--env $(AUTH_ENV),)


.PHONY: help guide init-env init-env-local init-env-compose init-env-local-force init-env-compose-force sync-runtime-secrets show-image-tags validate test build build-pipeline build-image build-risk-vllm-image rebuild-app rebuild-risk-vllm package start up compose-up compose-up-private compose-down-private preflight-compose ready ready-local ready-full check-ready smoke runtime-validate runtime-targets storage-paths project-inventory refresh-generated-reports auth-status auth-doctor auth-plan auth-apply monitoring-projection operator-status operator-reports live-evidence release-check release-check-full vllm-commands hf-config-check risk-vllm-config-check risk-vllm-patch-removal-check model-inventory model-list model-status model-validate model-diff model-propose-add model-propose-remove status stop down compose-down compose-logs logs compose-diagnostics clean clean-dry-run cleanup-plan remove-plan clean-all reset bootstrap first-run rebuild-full doctor reset-version infisical-up infisical-down infisical-logs infisical-init secrets-push secrets-push-sensitive secrets-pull secrets-status validate-docs docs-check reports-check feature-check feature-plan

help:
	@echo "$(PROJECT_NAME) $(CURRENT_VERSION)"
	@echo ""
	@echo "어디서 시작할지 모르면: docs/START_HERE.md"
	@echo "상세 가이드: docs/README.md"
	@echo "상황별 명령 추천: make guide"
	@echo "처음 시작: docs/operations/first_project_guide.md"
	@echo "빠른 실행: docs/operations/day0_quickstart.md"
	@echo "빌드 흐름: docs/development/build_ux.md"
	@echo ""
	@echo "── 환경 초기화 ────────────────────────────────────────────────"
	@echo "make guide                  # 상황별 명령 추천 가이드 출력"
	@echo "make init-env-compose       # compose용 .env 생성 (이미 있으면 건너뜀)"
	@echo "make init-env-local         # 로컬 app-only .env 생성 (이미 있으면 건너뜀)"
	@echo "make init-env-compose-force # .env 강제 재생성 (비밀키 재발급, 나머지는 보존)"
	@echo "make sync-runtime-secrets   # .env의 admin key를 Prometheus 토큰 파일로만 복구"
	@echo "make show-image-tags        # compose 권장 이미지 태그 출력"
	@echo ""
	@echo "── 검증·테스트·빌드 ────────────────────────────────────────"
	@echo "make validate      # 계약·스키마·정책·문서 정적 검증"
	@echo "make build-pipeline # 통합 파이프라인 빌드 (make build 별칭; 서비스 기동 없음)"
	@echo "make test          # 단위·계약 테스트 (pytest 결정론적 실행)"
	@echo "make build         # validate + test + 이미지 빌드 + 패키징 (서비스 기동 없음)"
	@echo "make build-image   # 플랫폼 Docker 이미지 빌드"
	@echo "make rebuild-app   # 플랫폼 이미지만 재빌드 (make build-image 별칭)"
	@echo "make build-risk-vllm-image # 고급: Kanana risk 전용 vLLM 이미지만 빌드"
	@echo "make rebuild-risk-vllm # risk vLLM 이미지만 재빌드 (별칭)"
	@echo "make package       # generated report를 static placeholder로 재생성한 뒤 dist/ 릴리스 ZIP 생성"
	@echo ""
	@echo "── 기동·종료 ───────────────────────────────────────────────"
	@echo "make start              # 로컬 Gateway·Risk Adapter 기동"
	@echo "make up                 # make start 별칭"
	@echo "make compose-up         # preflight 후 full-stack compose 기동"
	@echo "make compose-up-private # private-network compose로 host 노출 축소 기동"
	@echo "make preflight-compose  # compose 기동 전 Docker·GPU·포트·시크릿 확인"
	@echo "make stop               # 로컬 서비스 및 compose 스택 종료"
	@echo "make down               # make stop 별칭"
	@echo "make compose-down       # full-stack compose 스택만 종료"
	@echo "make compose-diagnostics # compose ps·로그·vLLM 장애 패턴 요약"
	@echo "make logs               # 로컬 Gateway·Risk Adapter 로그 tail"
	@echo ""
	@echo "── Readiness·상태 ──────────────────────────────────────────"
	@echo "make ready-local   # 로컬 app-only /health 확인 (vLLM 불필요)"
	@echo "make ready         # full-stack readiness 확인 (make ready-full 별칭)"
	@echo "make ready-full    # 실제 vLLM upstream까지 포함한 엄격 readiness + smoke"
	@echo "make smoke         # 배포된 서비스 대상 smoke 테스트"
	@echo "make runtime-validate # 라이브 runtime 검증 리포트 생성 (reports/runtime/)"
	@echo "make runtime-targets  # registry 기반 runtime target inventory 생성"
	@echo "make storage-paths    # 로컬 저장소/cache/report/secret 경로 inventory 생성"
	@echo "make project-inventory # 전체 파일/문서/관리 ownership inventory 생성"
	@echo "make auth-status [ENV=/tmp/candidate.env] # 인증/profile/admin/internal-service 상태 표시"
	@echo "make auth-doctor [ENV=/tmp/candidate.env] # 인증 설정 위험/불일치 진단"
	@echo "make auth-plan MODE=strict [ENV=/tmp/candidate.env] # 인증 profile 변경 계획 표시"
	@echo "make auth-apply MODE=strict [ENV=/tmp/candidate.env] # 인증 profile flag를 env에 적용"
	@echo "make monitoring-projection # registry 기반 Prometheus/Grafana projection 생성"
	@echo "make operator-status  # runtime targets + GPU budget + monitoring label 상태 번들 생성"
	@echo "make operator-reports # runtime-targets + storage-paths + project-inventory + monitoring-projection + operator-status + live-evidence"
	@echo "make refresh-generated-reports # package 전 current generated report 재생성"
	@echo "make live-evidence    # operator status + runtime validation evidence 번들 생성"
	@echo "make release-check    # 서비스 기동 없는 정적 릴리스 gate 실행"
	@echo "make release-check-full # release-check + deterministic tests"
	@echo "make status        # 프로세스·/health 상태 표시 (READY_MODE=full로 의존성 상세)"
	@echo ""
	@echo "── 진단·초기화·정리 ────────────────────────────────────────"
	@echo "make doctor        # Python·계약·bash 문법·환경·서비스 로컬 진단"
	@echo "make reset         # 통합 제거/초기화 (서비스 중지 + 플랫폼/risk 이미지 + 아티팩트)"
	@echo "                   #   PURGE_MODEL_CACHE=1  → model_cache/ 추가 삭제"
	@echo "                   #   PURGE_RUNTIME_SECRETS=1 → .runtime/ 추가 삭제"
	@echo "                   #   PURGE_VENV=1         → .venv/ 추가 삭제"
	@echo "make bootstrap     # 전체 재빌드 (.venv + deps + .env + validate + test + 플랫폼/risk 이미지 + risk config check)"
	@echo "make first-run     # 처음 full-stack 준비 (make bootstrap 별칭)"
	@echo "make rebuild-full  # 전체 재빌드 (make bootstrap 별칭)"
	@echo "                   #   HF_TOKEN=hf_xxx make first-run"
	@echo "                   #   AUTH_MODE=local_open HF_TOKEN=hf_xxx make first-run  → 인증 없이"
	@echo "make clean         # 생성 아티팩트 제거 (서비스 실행 중이면 거부)"
	@echo "make clean-dry-run # clean이 삭제할 항목 미리 보기"
	@echo "make cleanup-plan   # make clean-dry-run 별칭"
	@echo "make remove-plan    # 삭제 대상 미리 보기 (make cleanup-plan 별칭)"
	@echo "make clean-all     # 아티팩트·로그·선택적 대형 캐시 제거"
	@echo ""
	@echo "── 시크릿 관리 (Infisical) ─────────────────────────────────"
	@echo "make infisical-up         # Infisical 자체 호스팅 스택 기동 (웹 UI: :9420)"
	@echo "make infisical-down       # Infisical 스택 종료"
	@echo "make infisical-init       # Machine Identity 설정 가이드 출력"
	@echo "make secrets-push         # .env 전체 → Infisical 동기화"
	@echo "make secrets-push-sensitive # 민감 값(TOKEN/KEY/PASSWORD)만 → Infisical"
	@echo "make secrets-pull         # Infisical → .env 갱신"
	@echo "make secrets-status       # .env vs Infisical 상태 비교"
	@echo ""
	@echo "── 모델·버전 ────────────────────────────────────────────────"
	@echo "make vllm-commands        # 설정된 vLLM 기동 명령 출력"
	@echo "make hf-config-check      # HF AutoConfig만 로드해 vLLM/bnb 이전 config 문제 분리"
	@echo "make risk-vllm-config-check # 고급: RISK_VLLM_IMAGE 내부에서 Kanana config 파싱 검증"
	@echo "make risk-vllm-patch-removal-check # 고급: vendor patch 제거 후보 상태 점검"
	@echo "make model-inventory      # 모델 API·리소스 현황 표시"
	@echo "make model-list           # read-only modelctl list"
	@echo "make model-status         # read-only modelctl status"
	@echo "make model-validate       # registry/projection/lifecycle 검증"
	@echo "make model-diff           # registry projection drift 확인"
	@echo "make model-propose-add ID=new-model PORT=9499 ENDPOINT=/v1/new UPSTREAM=org/model ROLE=main_llm # 모델 추가 계획"
	@echo "make model-propose-remove ID=old-model # 모델 제거 계획"
	@echo "make reset-version NEW_VERSION=x.y.z # 프로젝트 버전 메타데이터 초기화"
	@echo ""
	@echo "── 문서·리포트 점검 ────────────────────────────────────────"
	@echo "make validate-docs          # docs-check + reports-check + feature-check 통합 실행"
	@echo "make docs-check             # Markdown 링크 유효성 검사 (파일 수정 없음)"
	@echo "make reports-check          # generated report 헤딩·내용·배너·버전 레이블 점검 (파일 수정 없음)"
	@echo "make feature-check          # features/*.yaml 매니페스트 정합성 점검 (파일 수정 없음)"
	@echo "make feature-plan [ID=<id>] # 기능 변경 시 갱신 대상 파일/테스트/명령 출력 (maintainer용)"

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
	$(PYTHON) scripts/config/setup_env.py --sync-runtime-secrets

show-image-tags:
	$(PYTHON) scripts/config/setup_env.py --show-image-tags

validate:
	$(PYTHON) scripts/build/check_python.py --context validate >/dev/null
	$(PYTHON) scripts/validation/validate_contracts.py
	$(MAKE) validate-docs

test:
	PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PYTHON) scripts/validation/run_tests.py -q

build:
	bash scripts/build/build_all.sh

build-pipeline: build

build-image:
	bash scripts/build/build_platform_image.sh

rebuild-app: build-image

build-risk-vllm-image:
	bash scripts/build/build_risk_vllm_image.sh

rebuild-risk-vllm: build-risk-vllm-image


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

compose-up-private:
	@if [[ ! -f .env ]]; then echo "오류: .env 파일이 없습니다. make init-env-compose 를 먼저 실행하세요." >&2; exit 2; fi
	SKIP_PREFLIGHT=1 docker compose -f ops/compose/full-stack.private-network.yaml --env-file .env up -d

compose-down-private:
	docker compose -f ops/compose/full-stack.private-network.yaml --env-file .env down

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
	@if [[ -z "$(MODE)" ]]; then echo "MODE=local_open|private_network|edge_terminated|strict 를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/auth/auth_plan.py $(AUTH_ENV_ARG) --mode $(MODE)

auth-apply:
	@if [[ -z "$(MODE)" ]]; then echo "MODE=local_open|private_network|edge_terminated|strict 를 지정하세요" >&2; exit 2; fi
	$(PYTHON) scripts/auth/auth_apply.py $(AUTH_ENV_ARG) --mode $(MODE) --yes

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
	docker compose -f ops/compose/full-stack.private-network.yaml --env-file .env down

compose-logs:
	docker compose -f ops/compose/full-stack.private-network.yaml --env-file .env logs -f --tail=100

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
