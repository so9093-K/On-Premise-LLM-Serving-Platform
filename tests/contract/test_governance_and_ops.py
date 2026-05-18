from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_gpu_resource_requirement_reference_is_packaged() -> None:
    doc = (ROOT / "docs/resources/gpu_resource_requirements_48gb.md").read_text(encoding="utf-8")
    assert "48GB VRAM 단일 GPU" in doc
    assert "0.825" in doc
    assert "runtime peak" in doc


def test_pytest_external_plugins_are_disabled() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = pyproject["tool"]["pytest"]["ini_options"].get("addopts", "")
    assert "-p no:cacheprovider" in addopts
    assert "-p no:ddtrace" in addopts
    assert "-p no:asyncio" in addopts
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "PYTHONDONTWRITEBYTECODE=1" in makefile
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in makefile


def test_ops_templates_exist_without_runtime_claims() -> None:
    prom = yaml.safe_load((ROOT / "ops/prometheus/prometheus.yml").read_text(encoding="utf-8"))
    jobs = {job["job_name"] for job in prom["scrape_configs"]}
    assert {"gateway", "risk-adapter", "vllm-runtimes", "dcgm-exporter", "cadvisor"}.issubset(jobs)
    vllm_job = next(job for job in prom["scrape_configs"] if job["job_name"] == "vllm-runtimes")
    labels = {
        config["labels"]["model"]: config["labels"]["runtime_service"]
        for config in vllm_job["static_configs"]
    }
    assert labels == {
        "local-main": "main-llm-vllm",
        "local-embed": "embedding-vllm",
        "local-colbert-ko": "colbert-ko-vllm",
        "risk-prompt": "risk-prompt-vllm",
    }
    compose = yaml.safe_load((ROOT / "ops/compose/full-stack.private-network.yaml").read_text(encoding="utf-8"))
    colbert_command = compose["services"]["colbert-ko-vllm"]["command"]
    assert "sigridjineth/colbert-ko-embeddinggemma-300m" not in colbert_command
    assert "/models/colbert-ko-vllm" in colbert_command
    assert "--pooler-config.task" in colbert_command
    assert "token_embed" in colbert_command

    for path in [
        "ops/grafana/dashboards/serving_home.json",
        "ops/grafana/dashboards/executive_runtime_overview.json",
        "ops/grafana/dashboards/gpu_capacity_and_oom_risk.json",
        "ops/grafana/dashboards/risk_signal_operations.json",
        "ops/grafana/dashboards/api_experience.json",
        "ops/grafana/dashboards/model_runtime_deep_dive.json",
        "ops/grafana/dashboards/observability_data_quality.json",
    ]:
        dashboard = json.loads((ROOT / path).read_text(encoding="utf-8"))
        assert dashboard["uid"]
        assert "contract-reference" in dashboard["tags"]
        assert dashboard["panels"]
        variable_names = {item["name"] for item in dashboard["templating"]["list"]}
        assert {"datasource", "window", "model", "runtime_service", "route", "status_code"}.issubset(variable_names)
        for panel in dashboard["panels"]:
            assert panel.get("datasource") == {"type": "prometheus", "uid": "${datasource}"}


def test_compose_gpu_budget_uses_configured_avoid_above() -> None:
    validator = (ROOT / "scripts/compose/validate_vllm_compose.py").read_text(encoding="utf-8")
    diagnostics = (ROOT / "scripts/compose/compose_diagnostics.sh").read_text(encoding="utf-8")
    assert "GPU_BUDGETS_PATH" in validator
    assert "avoid_above" in validator
    assert "0.90" not in validator
    assert "GPU_AVOID_ABOVE" in diagnostics
    assert "0.90" not in diagnostics


def test_runtime_validation_matrix_requires_validation() -> None:
    matrix = yaml.safe_load((ROOT / "harness/runtime_validation_matrix.yaml").read_text(encoding="utf-8"))
    assert matrix["validation_policy"] == "runtime_validation_required"
    assert {check["id"] for check in matrix["validation_checks"]} >= {"gateway-runtime", "risk-adapter-runtime", "vllm-runtime", "gpu-capacity", "monitoring-scrape"}


def test_operator_status_board_ux_is_user_facing() -> None:
    monitoring = yaml.safe_load((ROOT / "configs/monitoring.yaml").read_text(encoding="utf-8"))
    operator = monitoring["operator_status_ux"]
    assert operator["landing_dashboard"] == "gpu_capacity_and_oom_risk"
    assert operator["drill_down_order"][0] == "serving_home"
    assert "gpu_headroom" in operator["first_screen_order"]
    assert {item["mode"] for item in operator["serving_modes"]} == {
        "ACTIVE",
        "IDLE WARM",
        "IDLE COLD",
        "DEGRADED",
        "NO DATA",
    }
    assert {item["level"] for item in operator["status_levels"]} == {"green", "yellow", "red", "gray"}
    status_doc = (ROOT / "docs/operations/status_board_ux.md").read_text(encoding="utf-8")
    assert "지금 요청을 안전하게 처리할 수 있는가?" in status_doc
    exec_dashboard = json.loads((ROOT / "ops/grafana/dashboards/executive_runtime_overview.json").read_text(encoding="utf-8"))
    titles = {panel["title"] for panel in exec_dashboard["panels"]}
    assert {
        "Overall Status",
        "User Traffic",
        "p95 Latency (5m)",
        "Error Rate",
        "GPU Headroom",
        "Component Readiness",
        "Scrape Health",
    }.issubset(titles)
    assert exec_dashboard["panels"][0]["type"] == "text"
    assert "이 화면을 보는 법" in exec_dashboard["panels"][0]["options"]["content"]
    home = json.loads((ROOT / "ops/grafana/dashboards/serving_home.json").read_text(encoding="utf-8"))
    home_titles = {panel["title"] for panel in home["panels"]}
    assert {
        "Serving Home Operator Guide",
        "Serving Verdict",
        "User Traffic",
        "Scrape Targets",
        "GPU Headroom",
        "OOM / Restart",
        "Runtime Capacity",
        "Needs Attention",
    }.issubset(home_titles)
    assert len(home["panels"]) <= 20
    assert "IDLE WARM" in home["panels"][0]["options"]["content"]
    variables_by_name = {item["name"]: item for item in home["templating"]["list"]}
    home_variables = set(variables_by_name)
    assert "user_route" in home_variables
    user_route_text = json.dumps(variables_by_name["user_route"], ensure_ascii=False)
    assert "/v1/risk/.*" in user_route_text
    assert re.search(r"(?<!/v1)/risk/\.\*", user_route_text) is None
    user_route_options = {item["text"]: item["value"] for item in variables_by_name["user_route"]["options"]}
    assert user_route_options == {
        "All user routes": "/v1/chat/completions|/v1/embeddings|/v1/risk/.*|/v1/retrieval/.*",
        "Chat": "/v1/chat/completions",
        "Embeddings": "/v1/embeddings",
        "Risk": "/v1/risk/.*",
        "Retrieval": "/v1/retrieval/.*",
    }



def test_grafana_panels_include_operator_descriptions() -> None:
    for path in [
        "ops/grafana/dashboards/serving_home.json",
        "ops/grafana/dashboards/executive_runtime_overview.json",
        "ops/grafana/dashboards/gpu_capacity_and_oom_risk.json",
        "ops/grafana/dashboards/risk_signal_operations.json",
        "ops/grafana/dashboards/api_experience.json",
        "ops/grafana/dashboards/model_runtime_deep_dive.json",
        "ops/grafana/dashboards/observability_data_quality.json",
    ]:
        dashboard = json.loads((ROOT / path).read_text(encoding="utf-8"))
        for panel in dashboard["panels"]:
            description = panel.get("description", "")
            assert description
            assert any(token in description for token in ["Healthy", "Attention", "Action Required", "No Runtime Data", "No Data", "Action"])


def test_grafana_dashboards_are_english_titled_variable_backed_and_streaming_aware() -> None:
    dashboards = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in (ROOT / "ops/grafana/dashboards").glob("*.json")}
    assert set(dashboards) >= {
        "serving_home.json",
        "executive_runtime_overview.json",
        "gpu_capacity_and_oom_risk.json",
        "risk_signal_operations.json",
        "api_experience.json",
        "model_runtime_deep_dive.json",
        "observability_data_quality.json",
    }
    for dashboard in dashboards.values():
        assert " / " not in dashboard["title"]
        variables = {item["name"] for item in dashboard["templating"]["list"]}
        assert {"datasource", "window", "model", "runtime_service", "route", "status_code"}.issubset(variables)
        assert dashboard["panels"]
        for panel in dashboard["panels"]:
            if panel["type"] == "stat" and panel.get("options", {}).get("colorMode") == "background":
                assert panel["options"].get("graphMode") == "none"
            if panel["type"] == "timeseries":
                assert panel.get("options", {}).get("legend", {}).get("showLegend") is True
    api_text = json.dumps(dashboards["api_experience.json"], ensure_ascii=False)
    for metric in ["streaming_chunks_total", "streaming_bytes_total", "streaming_errors_total", "streaming_usage_events_total"]:
        assert metric in api_text
    executive_text = json.dumps(dashboards["executive_runtime_overview.json"], ensure_ascii=False)
    assert "clamp_min(sum(increase(http_requests_total" in executive_text
    assert "clamp_min(sum(rate(http_requests_total" not in executive_text
    assert 'path=~\\"chat/completions(:stream)?\\"' in api_text
    home_text = json.dumps(dashboards["serving_home.json"], ensure_ascii=False)
    assert 'route=~\\"$user_route\\"' in home_text
    assert "Needs Attention" in home_text
    assert "ai_serving_verdict_code" in home_text
    assert "backend_restart_total" not in home_text
    assert "gpu_oom_events_total" not in home_text
    assert "container_oom_events_total" in home_text
    assert "container_start_time_seconds" in home_text
    home_panels = {panel["title"]: panel for panel in dashboards["serving_home.json"]["panels"]}
    oom_panel_text = json.dumps(home_panels["OOM / Restart"], ensure_ascii=False)
    assert "or vector(0)" not in oom_panel_text
    user_traffic_text = json.dumps(home_panels["User Traffic"], ensure_ascii=False)
    assert 'service=\\"gateway\\"' in user_traffic_text
    assert 'status_code=~\\"$status_code\\"' not in user_traffic_text
    for dashboard_name, dashboard in dashboards.items():
        if dashboard_name != "serving_home.json":
            assert any(link.get("title") == "Serving Home" for link in dashboard.get("links", []))
        assert "Home dashboard — GPU" not in json.dumps(dashboard, ensure_ascii=False)
    gpu_text = json.dumps(dashboards["gpu_capacity_and_oom_risk.json"], ensure_ascii=False)
    assert "backend_restart_total" not in gpu_text
    assert "gpu_oom_events_total" not in gpu_text
    assert "container_oom_events_total" in gpu_text
    assert "container_start_time_seconds" in gpu_text


def test_endpoint_reference_matches_monitoring_dashboard_inventory() -> None:
    endpoint_doc = (ROOT / "docs/operations/endpoint_reference.md").read_text(encoding="utf-8")
    for dashboard_id in [
        "serving_home",
        "executive_runtime_overview",
        "api_experience",
        "model_runtime_deep_dive",
        "gpu_capacity_and_oom_risk",
        "risk_signal_operations",
        "observability_data_quality",
    ]:
        assert f"`{dashboard_id}`" in endpoint_doc
    assert "clamp_min(sum(rate(http_requests_total" not in endpoint_doc
    assert "allowUiUpdates=false" in endpoint_doc


def test_runtime_validation_matrix_has_actionable_fields() -> None:
    matrix = yaml.safe_load((ROOT / "harness/runtime_validation_matrix.yaml").read_text(encoding="utf-8"))
    for check in matrix["validation_checks"]:
        assert check["owner"]
        assert check["validation"]
        assert check["artifact_file"].startswith("reports/runtime/")
        assert check["operator_action"]
        assert check["runtime_validation_required"] is True


def test_build_ux_separates_build_from_runtime_startup() -> None:
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    assert 'make build         # build artifacts/images only; does not start or keep services alive' in makefile
    for target in ['start:', 'up:', 'ready:', 'check-ready:', 'status:', 'stop:', 'down:', 'logs:', 'build-pipeline:', 'first-run:', 'rebuild-full:', 'rebuild-app:', 'rebuild-risk-vllm:', 'rebuild-colbert-ko-vllm:', 'remove-plan:']:
        assert target in makefile

    build_doc = (ROOT / 'docs/development/build_ux.md').read_text(encoding='utf-8')
    assert 'build, start, readiness, deploy, release는 서로 다른 동작이다.' in build_doc
    assert '`make build`는 artifact/image를 생성하고 검증한다.' in build_doc
    assert '`make start`는 local service 또는 compose stack을 시작한다.' in build_doc
    assert '`make ready`는 live stack readiness를 증명한다.' in build_doc

    scripts_doc = (ROOT / 'scripts/README.md').read_text(encoding='utf-8')
    assert 'build와 runtime은 다른 단계다' in scripts_doc
    assert '`make build`는 서비스를 시작하지 않는다' in scripts_doc
    assert 'make build-pipeline' in build_doc
    assert 'make first-run' in build_doc
    assert 'make remove-plan' in build_doc
    assert (ROOT / 'docs/operations/first_project_guide.md').exists()


def test_runtime_ux_scripts_exist_and_are_executable() -> None:
    import os
    for rel in [
        'scripts/ops/start_services.sh',
        'scripts/ops/up_services.sh',
        'scripts/ops/stop_services.sh',
        'scripts/ops/down_services.sh',
        'scripts/ops/status_services.sh',
        'scripts/ops/ready_check.sh',
        'scripts/ops/check_ready.sh',
    ]:
        path = ROOT / rel
        assert path.exists()
        assert os.access(path, os.X_OK)


def test_command_terminology_policy_is_shared_and_enforced() -> None:
    policy_doc = (ROOT / 'docs/governance/policies/command_terminology_policy.md').read_text(encoding='utf-8')
    policy = yaml.safe_load((ROOT / 'configs/command_terminology_policy.yaml').read_text(encoding='utf-8'))
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')

    assert policy['policy_name'] == 'command_terminology_policy'
    assert policy['principle'] == 'standard_command_semantics'
    assert policy['canonical_commands']['build']['starts_services'] is False
    assert policy['canonical_commands']['start']['starts_services'] is True
    assert policy['canonical_commands']['ready']['starts_services'] is False

    for command, spec in policy['canonical_commands'].items():
        if spec.get('make_target_required'):
            assert f'{command}:' in makefile
    for alias, spec in policy['aliases'].items():
        if spec.get('make_target_required'):
            assert f'{alias}:' in makefile
            assert spec['canonical'] in policy['canonical_commands']

    for phrase in policy['required_documentation_phrases']:
        assert phrase in policy_doc

    terminology = (ROOT / 'docs/governance/terminology.md').read_text(encoding='utf-8')
    for term in ['build', 'start', 'up', 'down', 'ready', 'smoke', 'package', 'release', 'deploy']:
        assert term in terminology


def test_p2_runtime_validation_harness_is_packaged() -> None:
    import os
    import subprocess
    import sys

    runtime_script = ROOT / "scripts/validation/runtime_validation.py"
    render_script = ROOT / "scripts/models/render_vllm_commands.py"
    assert runtime_script.exists()
    assert render_script.exists()
    assert os.access(runtime_script, os.X_OK)
    assert os.access(render_script, os.X_OK)

    runtime_text = runtime_script.read_text(encoding="utf-8")
    assert "reports/runtime" in runtime_text
    assert "nvidia-smi" in runtime_text
    assert "/api/v1/targets" in runtime_text
    assert "FORBIDDEN_RISK_FIELDS" in runtime_text

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "runtime-validate:" in makefile
    assert "vllm-commands:" in makefile

    plan = (ROOT / "harness/runtime_validation_plan.md").read_text(encoding="utf-8")
    assert "Runtime Validation Plan" in plan
    assert "Validation Report Rule" in plan

    output = subprocess.check_output(
        [sys.executable, "scripts/models/render_vllm_commands.py", "--service", "main_llm"],
        cwd=ROOT,
        text=True,
    )
    assert "--served-model-name local-main" in output
    assert "--max-model-len 16384" in output
    assert "--enable-prefix-caching" in output
    assert "--prefix-caching-hash-algo sha256_cbor" in output
    assert "--enable-auto-tool-choice" in output
    assert "--tool-call-parser gemma4" in output
    assert "--reasoning-parser gemma4" in output
    assert "--chat-template" in output



def test_full_stack_compose_and_prometheus_paths_are_network_correct() -> None:
    prom = (ROOT / "ops/prometheus/prometheus.yml").read_text(encoding="utf-8")
    assert "/etc/prometheus/rules/model_runtime.rules.yml" in prom
    assert "dcgm-exporter:9400" in prom
    assert "dcgm-exporter:9412" not in prom
    assert "cadvisor:8080" in prom
    assert 'model: local-main' in prom
    assert 'runtime_service: main-llm-vllm' in prom

    rules = (ROOT / "ops/prometheus/rules/model_runtime.rules.yml").read_text(encoding="utf-8")
    assert "vllm_container_memory_usage_bytes" in rules
    assert "vllm_container_cpu_usage_ratio" in rules
    assert "container_label_com_docker_compose_service" in rules
    assert "vllm:kv_cache_usage_perc" in rules
    assert "(vllm:kv_cache_usage_perc <= 1)" in rules
    assert "((vllm:kv_cache_usage_perc > 1) / 100)" in rules

    compose = (ROOT / "ops/compose/full-stack.example.yaml").read_text(encoding="utf-8")
    assert "dcgm-exporter:" in compose
    assert "cadvisor:" in compose
    assert (
        "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH: "
        "/var/lib/grafana/dashboards/gpu_capacity_and_oom_risk.json"
    ) in compose
    assert "${DCGM_EXPORTER_IMAGE:" in compose
    assert "${DCGM_EXPORTER_PORT:-9412}:9400" in compose or "9412:9400" in compose
    assert "${CADVISOR_PORT:-9413}:8080" in compose or "9413:8080" in compose
    assert "env_file: ../../.env" in compose
    assert "copy to compose.yaml" not in compose.lower()

    private_compose = (ROOT / "ops/compose/full-stack.private-network.yaml").read_text(encoding="utf-8")
    assert (
        "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH: "
        "/var/lib/grafana/dashboards/gpu_capacity_and_oom_risk.json"
    ) in private_compose
    assert "${GATEWAY_BIND_ADDR:-0.0.0.0}:${GATEWAY_PORT:-9400}:9400" in private_compose

    full_stack_doc = (ROOT / "docs/operations/full_stack_runtime.md").read_text(encoding="utf-8")
    assert "docker compose -f ops/compose/full-stack.private-network.yaml --env-file .env up" in full_stack_doc
    assert "dcgm-exporter:9400" in full_stack_doc
    assert "gitlab_cicd_deployment.md" in full_stack_doc


def test_gitlab_ci_deployment_contract_is_documented_and_operationally_safe() -> None:
    ci = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts/ci/deploy_gitlab_compose.sh").read_text(encoding="utf-8")
    doc = (ROOT / "docs/operations/gitlab_cicd_deployment.md").read_text(encoding="utf-8")

    assert "docker:27.5.1-dind" in ci
    assert 'PLATFORM_IMAGE_VERSION="$CI_REGISTRY_IMAGE/platform:release_${APP_VERSION}"' in ci
    assert "release" in ci

    assert 'REGISTRY_USER="${REGISTRY_DEPLOY_USER:-${CI_REGISTRY_USER:-}}"' in deploy
    assert 'REGISTRY_PASSWORD="${REGISTRY_DEPLOY_PASSWORD:-${CI_REGISTRY_PASSWORD:-}}"' in deploy
    assert 'DEPLOY_MODE="${DEPLOY_MODE:-rolling}"' in deploy
    assert "rolling|full" in deploy
    assert "--exclude \".env\"" in deploy
    assert "--exclude \".runtime/\"" in deploy
    assert "--exclude \"model_cache/\"" in deploy
    assert 'docker compose -f "${COMPOSE_FILE}" --env-file .env up -d --remove-orphans' in deploy
    assert 'docker compose -f "${COMPOSE_FILE}" --env-file .env up -d --no-deps gateway risk-adapter' in deploy
    assert 'get_env_value GATEWAY_BIND_ADDR' in deploy
    assert 'GATEWAY_PROBE_HOST="localhost"' in deploy
    assert 'HEALTH_URL="${GATEWAY_HEALTH_URL:-http://${GATEWAY_PROBE_HOST}:${GATEWAY_PORT}/health}"' in deploy
    assert 'PRUNE_DANGLING_IMAGES="${PRUNE_DANGLING_IMAGES:-1}"' in deploy
    assert 'docker image prune -f --filter dangling=true' in deploy
    preflight_compose = (ROOT / "scripts/compose/preflight_compose.sh").read_text(encoding="utf-8")
    assert "COLBERT_KO_MODEL_DIR_RESOLVED" in preflight_compose, (
        "local compose preflight must validate COLBERT_KO_MODEL_DIR for reproducible local full-stack runs"
    )
    assert "prepare_colbert_ko_vllm_artifact.py --output-dir" in preflight_compose, (
        "local compose preflight must tell operators how to prepare the ColBERT-ko artifact"
    )
    assert "raw Hugging Face cache" in preflight_compose, (
        "local compose preflight must distinguish prepared ColBERT artifact from raw HF cache"
    )

    assert "Docker executor" in doc
    assert "Shell executor" in doc
    assert "REGISTRY_DEPLOY_USER" in doc
    assert "DEPLOY_MODE=rolling" in doc
    assert "DEPLOY_MODE=full" in doc
    assert "GATEWAY_BIND_ADDR=<175 internal IP>" in doc
    assert "PRUNE_DANGLING_IMAGES" in doc


def test_env_bootstrap_and_image_tag_automation_are_present() -> None:
    import os
    setup = ROOT / 'scripts/config/setup_env.py'
    build_image = ROOT / 'scripts/build/build_platform_image.sh'
    build_risk_image = ROOT / 'scripts/build/build_risk_vllm_image.sh'
    build_colbert_image = ROOT / 'scripts/build/build_colbert_ko_vllm_image.sh'
    check_risk_image = ROOT / 'scripts/models/check_risk_vllm_image_config.sh'
    assert setup.exists()
    assert build_image.exists()
    assert build_risk_image.exists()
    assert build_colbert_image.exists(), "scripts/build/build_colbert_ko_vllm_image.sh must exist"
    assert check_risk_image.exists()
    assert os.access(setup, os.X_OK)
    assert os.access(build_image, os.X_OK)
    assert os.access(build_risk_image, os.X_OK)
    assert os.access(build_colbert_image, os.X_OK), "scripts/build/build_colbert_ko_vllm_image.sh must be executable"
    assert os.access(check_risk_image, os.X_OK)

    build_colbert_text = build_colbert_image.read_text(encoding="utf-8")
    assert ":-${VLLM_BASE_IMAGE" not in build_colbert_text, (
        "local ColBERT-ko image build must not read CI-only VLLM_BASE_IMAGE"
    )
    assert (
        'BASE_IMAGE="${COLBERT_KO_VLLM_BASE_IMAGE:-${VLLM_IMAGE:-vllm/vllm-openai:gemma4-0505-cu129}}"'
        in build_colbert_text
    ), (
        "local ColBERT-ko build base image precedence must be "
        "COLBERT_KO_VLLM_BASE_IMAGE > VLLM_IMAGE > default"
    )

    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    for target in ['init-env:', 'init-env-local:', 'init-env-compose:', 'show-image-tags:', 'build-image:', 'build-risk-vllm-image:', 'build-colbert-ko-vllm-image:', 'risk-vllm-config-check:', 'compose-up:', 'compose-down:']:
        assert target in makefile

    images = yaml.safe_load((ROOT / 'configs/recommended_images.yaml').read_text(encoding='utf-8'))['images']
    assert images['platform']['default'] == f"ai-model-serving-platform:{(ROOT / 'VERSION').read_text(encoding='utf-8').strip()}"
    assert images['vllm']['default'].startswith('vllm/vllm-openai:gemma4')
    assert images['risk_vllm']['default'].startswith('ai-model-serving-risk-vllm-kanana:')
    assert images['risk_vllm']['compatibility_pins']['transformers'] == '4.52.4'
    assert images['dcgm_exporter']['default'].startswith('nvcr.io/nvidia/k8s/dcgm-exporter:')
    assert images['prometheus']['default'].startswith('prom/prometheus:v3')
    assert images['grafana']['default'].startswith('grafana/grafana:12.2')
    build_script = (ROOT / 'scripts/build/build_risk_vllm_image.sh').read_text(encoding='utf-8')
    assert 'MIN_TRANSFORMERS_VERSION="4.52.4"' in build_script
    assert 'load_local_env .env' in build_script
    assert 'below the Kanana minimum' in build_script
    dockerignore = (ROOT / '.dockerignore').read_text(encoding='utf-8')
    assert 'ops/*' in dockerignore
    assert '!ops/docker/Dockerfile.risk-vllm-kanana' in dockerignore
    assert '!ops/patches/transformers_llama_head_dim_guard.py' in dockerignore


def test_compose_env_example_has_reviewed_image_defaults() -> None:
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    env = (ROOT / '.env.compose.example').read_text(encoding='utf-8')
    assert f'PLATFORM_IMAGE=ai-model-serving-platform:{version}' in env
    assert 'VLLM_IMAGE=vllm/vllm-openai:gemma4' in env
    assert 'RISK_VLLM_IMAGE=ai-model-serving-risk-vllm-kanana:' in env
    assert 'RISK_VLLM_TRANSFORMERS_VERSION=4.52.4' in env
    assert 'COLBERT_KO_VLLM_IMAGE=ai-model-serving-colbert-ko-vllm:' in env, (
        ".env.compose.example must include COLBERT_KO_VLLM_IMAGE with local build default"
    )
    assert 'COLBERT_KO_MODEL_DIR=' in env, (
        ".env.compose.example must include COLBERT_KO_MODEL_DIR"
    )
    assert 'COLBERT_KO_MODEL_DIR=/opt/acl-ai-gateway/models/colbert-ko-vllm' in env, (
        ".env.compose.example must recommend an absolute production ColBERT-ko prepared artifact path"
    )
    assert 'COLBERT_KO_MODEL_DIR=./models/colbert-ko-vllm' not in env, (
        ".env.compose.example must not recommend relative COLBERT_KO_MODEL_DIR for production full deploy"
    )
    assert 'DCGM_EXPORTER_IMAGE=nvcr.io/nvidia/k8s/dcgm-exporter:' in env
    assert 'PROMETHEUS_IMAGE=prom/prometheus:v3-distroless' in env
    assert 'GRAFANA_IMAGE=grafana/grafana:12.2' in env
    assert 'Replace mutable tags with validated immutable digests before production promotion.' in env

def test_compose_up_syncs_runtime_secrets_before_docker_compose() -> None:
    script = (ROOT / "scripts/compose/compose_up.sh").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "bash scripts/compose/compose_up.sh" in makefile
    assert "--sync-runtime-secrets" in script
    assert ".runtime/prometheus/admin_api_key" in script
    assert '! -f "$PROM_SECRET" || ! -s "$PROM_SECRET"' in script
    assert "SKIP_PREFLIGHT=1 bash scripts/compose/compose_up.sh" in makefile
    assert "docker compose -f" in script


def test_prometheus_admin_token_uses_compose_secret_not_bind_mount() -> None:
    for rel in [
        "ops/compose/full-stack.example.yaml",
        "ops/compose/full-stack.private-network.yaml",
    ]:
        compose = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
        prometheus_secrets = compose["services"]["prometheus"]["secrets"]
        assert {"source": "prometheus_admin_api_key", "target": "admin_api_key"} in prometheus_secrets
        assert compose["secrets"]["prometheus_admin_api_key"]["file"] == "../../.runtime/prometheus/admin_api_key"
        assert all(
            not (isinstance(volume, str) and ".runtime/prometheus/admin_api_key" in volume)
            for volume in compose["services"]["prometheus"].get("volumes", [])
        )


def test_smoke_test_respects_python_bin() -> None:
    script = (ROOT / "scripts/ops/smoke_test.sh").read_text(encoding="utf-8")
    assert 'PYTHON_BIN="${PYTHON_BIN:-' in script
    assert 'command -v python3' in script
    assert '"$PYTHON_BIN" - <<' in script
    assert '"$PYTHON_BIN" - "$check_name" "$tmp_json"' in script


def test_config_version_semantics_are_explicit() -> None:
    for rel in [
        "configs/model_catalog.yaml",
        "configs/monitoring.yaml",
        "harness/runtime_validation_matrix.yaml",
    ]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "version_semantics:" in text
        assert "not package release version" in text


def test_dashboard_navigation_links_are_nonempty_and_uid_valid() -> None:
    dashboards_dir = ROOT / "ops/grafana/dashboards"
    dashboards = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in dashboards_dir.glob("*.json")
    }
    valid_uids = {d["uid"] for d in dashboards.values()}

    for name, dashboard in dashboards.items():
        links = dashboard.get("links", [])
        assert links, f"Dashboard '{name}' has no navigation links"
        for link in links:
            url = link.get("url", "")
            assert "/d/" in url, f"Dashboard '{name}' link has unexpected URL format: {url}"
            target_uid = url.split("/d/")[-1].split("/")[0]
            assert target_uid in valid_uids, (
                f"Dashboard '{name}' links to unknown uid '{target_uid}' (url={url})"
            )


def test_no_mixed_unit_panel_titles() -> None:
    chat = json.loads(
        (ROOT / "ops/grafana/dashboards/api_experience.json").read_text(encoding="utf-8")
    )
    model = json.loads(
        (ROOT / "ops/grafana/dashboards/model_runtime_deep_dive.json").read_text(encoding="utf-8")
    )
    chat_titles = {panel["title"] for panel in chat["panels"]}
    model_titles = {panel["title"] for panel in model["panels"]}

    assert "Streaming Chunks and Bytes" not in chat_titles, \
        "Mixed-unit panel 'Streaming Chunks and Bytes' must be split"
    assert "Streaming Duration and Chunks" not in chat_titles, \
        "Mixed-unit panel 'Streaming Duration and Chunks' must be split"
    assert "Queue and KV Trend" not in model_titles, \
        "Mixed-unit panel 'Queue and KV Trend' must be split"


def test_overall_status_does_not_use_max() -> None:
    dashboard = json.loads(
        (ROOT / "ops/grafana/dashboards/executive_runtime_overview.json").read_text(encoding="utf-8")
    )
    overall_panels = [p for p in dashboard["panels"] if p["title"] == "Overall Status"]
    assert overall_panels, "Overall Status panel not found in executive_runtime_overview"
    for panel in overall_panels:
        for target in panel.get("targets", []):
            expr = target.get("expr", "")
            assert "max(overall_runtime_status)" not in expr, (
                "Overall Status must use min(), not max(), to surface any unhealthy service"
            )


def test_monitoring_ux_streaming_label_is_status_not_result() -> None:
    monitoring_ux = (ROOT / "docs/operations/monitoring_ux.md").read_text(encoding="utf-8")
    assert "target,result}" not in monitoring_ux, \
        "monitoring_ux.md must use 'status' label, not 'result', for streaming metrics"
    assert ",result}" not in monitoring_ux, \
        "monitoring_ux.md must use 'status' label, not 'result', for streaming metrics"


def test_monitoring_ux_has_ttft_metric_as_current_dashboard_metric() -> None:
    monitoring_ux = (ROOT / "docs/operations/monitoring_ux.md").read_text(encoding="utf-8")
    assert "streaming_time_to_first_chunk_seconds_bucket" in monitoring_ux, \
        "monitoring_ux.md must document streaming_time_to_first_chunk_seconds_bucket"
    assert "다음 항목은 현재 metric만으로 정확히 계산하지 않는다" not in monitoring_ux, \
        "Stale 'not calculated' text must be removed — TTFT/duration/chunks are now in the dashboard"


def test_dashboard_panel_datasource_uses_variable() -> None:
    for path in (ROOT / "ops/grafana/dashboards").glob("*.json"):
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for panel in dashboard["panels"]:
            assert panel.get("datasource") == {"type": "prometheus", "uid": "${datasource}"}, (
                f"Panel '{panel.get('title')}' in {path.name} "
                f"must use datasource {{\"type\":\"prometheus\",\"uid\":\"${{datasource}}\"}}"
            )


def test_dashboard_json_has_no_raw_prompt_or_generated_text_in_exprs() -> None:
    forbidden_label_patterns = ["prompt=", "generated_text=", "raw_input=", "user_text="]
    for path in (ROOT / "ops/grafana/dashboards").glob("*.json"):
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                expr = target.get("expr", "")
                for pattern in forbidden_label_patterns:
                    assert pattern not in expr, (
                        f"Forbidden label selector '{pattern}' found in panel "
                        f"'{panel.get('title')}' expr in {path.name}"
                    )


def test_validate_grafana_promql_script_exists() -> None:
    script = ROOT / "scripts/validation/validate_grafana_promql.py"
    assert script.exists(), "scripts/validation/validate_grafana_promql.py must exist"
    content = script.read_text(encoding="utf-8")
    assert "/api/v1/query" in content
    assert "--config-only" in content
    assert "--allow-failures" in content


def test_colbert_ko_vllm_image_is_required_env_in_production_compose() -> None:
    """Production compose must use :? required syntax — no local-only :-fallback for colbert-ko-vllm."""
    for rel in [
        "ops/compose/full-stack.private-network.yaml",
        "ops/compose/full-stack.example.yaml",
    ]:
        raw = (ROOT / rel).read_text(encoding="utf-8")
        assert "COLBERT_KO_VLLM_IMAGE:?" in raw, (
            f"{rel}: COLBERT_KO_VLLM_IMAGE must use :? required syntax, "
            f"not :- with a local-only fallback"
        )
        assert ":-ai-model-serving-colbert-ko-vllm" not in raw, (
            f"{rel}: must not contain local-only colbert-ko-vllm image fallback"
        )
        assert "COLBERT_KO_MODEL_DIR:?" in raw, (
            f"{rel}: COLBERT_KO_MODEL_DIR must use :? required syntax, "
            f"not :- with a relative path fallback"
        )


def test_production_compose_files_have_no_build_blocks() -> None:
    """Build blocks belong only in the local-build override, never in production compose."""
    for rel in [
        "ops/compose/full-stack.private-network.yaml",
        "ops/compose/full-stack.example.yaml",
    ]:
        compose = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
        for svc_name, svc in compose["services"].items():
            assert "build" not in svc, (
                f"{rel}: service '{svc_name}' must not have a 'build' block. "
                f"Use ops/compose/full-stack.local-build.yaml for local builds."
            )


def test_local_build_override_compose_exists_with_colbert_build_block() -> None:
    """ops/compose/full-stack.local-build.yaml must exist and provide a build block for colbert-ko-vllm."""
    local_build = ROOT / "ops/compose/full-stack.local-build.yaml"
    assert local_build.exists(), (
        "ops/compose/full-stack.local-build.yaml does not exist. "
        "Create it with a build override for local colbert-ko-vllm development."
    )
    compose = yaml.safe_load(local_build.read_text(encoding="utf-8"))
    colbert = compose.get("services", {}).get("colbert-ko-vllm", {})
    assert "build" in colbert, (
        "ops/compose/full-stack.local-build.yaml: colbert-ko-vllm must have a 'build' block"
    )
    assert colbert.get("build", {}).get("dockerfile") == "ops/docker/Dockerfile.colbert-ko-vllm", (
        "ops/compose/full-stack.local-build.yaml: build.dockerfile must point to "
        "ops/docker/Dockerfile.colbert-ko-vllm"
    )


def test_gitlab_ci_has_colbert_ko_vllm_build_job() -> None:
    """GitLab CI must build colbert-ko-vllm as a required dedicated retrieval runtime.

    Both risk-vllm-kanana and colbert-ko-vllm are built in a single build-vllm-derived
    job to avoid pulling the large vLLM base image twice.  Shell logic lives in
    scripts/ci/build_vllm_derived_images.sh.

    build-vllm-derived runs automatically on release/tag pipelines when BUILD_VLLM_DERIVED=1
    or DEPLOY_MODE=full is set.  Rolling app deploys skip it entirely.
    allow_failure: false — if the job runs and fails, it is a release failure.
    """
    ci = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    ci_parsed = yaml.safe_load(ci)

    build_script_path = ROOT / "scripts/ci/build_vllm_derived_images.sh"
    assert build_script_path.exists(), (
        "scripts/ci/build_vllm_derived_images.sh must exist — "
        "build-vllm-derived delegates its shell logic here"
    )
    build_script = build_script_path.read_text(encoding="utf-8")

    assert "build-risk-vllm:" not in ci, (
        ".gitlab-ci.yml must not keep the old standalone build-risk-vllm job; "
        "use build-vllm-derived instead"
    )
    assert "build-colbert-ko-vllm:" not in ci, (
        ".gitlab-ci.yml must not keep the old standalone build-colbert-ko-vllm job; "
        "use build-vllm-derived instead"
    )
    assert "build-vllm-derived:" in ci, (
        ".gitlab-ci.yml must have a 'build-vllm-derived' job that builds both "
        "risk-vllm-kanana and colbert-ko-vllm from a shared base image pull"
    )
    assert "VLLM_BASE_IMAGE" in ci, (
        ".gitlab-ci.yml must define VLLM_BASE_IMAGE as the common base for all vLLM-derived images"
    )
    assert "COLBERT_KO_VLLM_IMAGE_SHA" in ci, (
        ".gitlab-ci.yml must define COLBERT_KO_VLLM_IMAGE_SHA variable"
    )

    build_job = ci_parsed.get("build-vllm-derived", {})

    assert build_job.get("when") != "manual", (
        "build-vllm-derived must not be a manual job — it runs automatically when "
        "BUILD_VLLM_DERIVED=1 or DEPLOY_MODE=full is set on a release/tag pipeline"
    )
    assert build_job.get("allow_failure") is False, (
        "build-vllm-derived must have allow_failure: false explicitly — "
        "if it runs and fails, it is a release failure"
    )

    # only: refs + variables — job runs on matching ref AND matching variable
    only = build_job.get("only", {})
    assert isinstance(only, dict), (
        "build-vllm-derived only: must use refs+variables form to gate on pipeline intent variable"
    )
    only_vars = only.get("variables", [])
    assert any("BUILD_VLLM_DERIVED" in v for v in only_vars), (
        "build-vllm-derived only.variables must include BUILD_VLLM_DERIVED == \"1\" "
        "so build-only pipelines can trigger the derived image build"
    )
    assert any("DEPLOY_MODE" in v for v in only_vars), (
        "build-vllm-derived only.variables must include DEPLOY_MODE == \"full\" "
        "as an alternative trigger for full runtime deploy pipelines"
    )

    build_job_script = " ".join(str(s) for s in build_job.get("script", []))
    assert "build_vllm_derived_images.sh" in build_job_script, (
        "build-vllm-derived must delegate to scripts/ci/build_vllm_derived_images.sh"
    )

    assert "set -euo pipefail" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must use 'set -euo pipefail'"
    )
    assert "Dockerfile.risk-vllm-kanana" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must reference ops/docker/Dockerfile.risk-vllm-kanana"
    )
    assert "Dockerfile.colbert-ko-vllm" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must reference ops/docker/Dockerfile.colbert-ko-vllm"
    )
    assert "docker pull \"${RESOLVED_VLLM_BASE_IMAGE}\"" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must pull the resolved shared vLLM base image once "
        "before building risk and colbert derived images"
    )
    for image_var in [
        "RISK_VLLM_IMAGE_SHA",
        "RISK_VLLM_IMAGE_REF",
        "COLBERT_KO_VLLM_IMAGE_SHA",
        "COLBERT_KO_VLLM_IMAGE_REF",
    ]:
        assert f'docker push "${{{image_var}}}"' in build_script, (
            f"scripts/ci/build_vllm_derived_images.sh must push {image_var}"
        )

    assert "risk-vllm-kanana:${CI_COMMIT_TAG}" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must push risk-vllm-kanana:<tag> on CI_COMMIT_TAG pipelines"
    )
    assert "colbert-ko-vllm:${CI_COMMIT_TAG}" in build_script, (
        "scripts/ci/build_vllm_derived_images.sh must push colbert-ko-vllm:<tag> on CI_COMMIT_TAG pipelines"
    )

    deploy_job = ci_parsed.get("deploy-gpu-175", {})
    deploy_script_text = " ".join(str(s) for s in deploy_job.get("script", []))
    assert "RISK_VLLM_IMAGE_SHA" in deploy_script_text, (
        "deploy-gpu-175 script must use RISK_VLLM_IMAGE_SHA as the default image "
        "for DEPLOY_MODE=full when no explicit override is given"
    )
    assert "COLBERT_KO_VLLM_IMAGE_SHA" in deploy_script_text, (
        "deploy-gpu-175 script must use COLBERT_KO_VLLM_IMAGE_SHA as the default image "
        "for DEPLOY_MODE=full when no explicit override is given"
    )

    deploy = (ROOT / "scripts/ci/deploy_gitlab_compose.sh").read_text(encoding="utf-8")
    assert "COLBERT_KO_VLLM_IMAGE_TO_DEPLOY" in deploy, (
        "deploy_gitlab_compose.sh must support COLBERT_KO_VLLM_IMAGE_TO_DEPLOY override"
    )
    assert "vLLM image overrides are allowed only with DEPLOY_MODE=full" in deploy, (
        "deploy_gitlab_compose.sh must fail fast when rolling deploy receives "
        "RISK_VLLM_IMAGE_TO_DEPLOY or COLBERT_KO_VLLM_IMAGE_TO_DEPLOY"
    )
    assert "Rolling deploy only updates gateway and risk-adapter" in deploy, (
        "rolling override error must explain that rolling deploy updates only gateway/risk-adapter"
    )
    assert "Full runtime deploy requires DEPLOY_MODE=full" in deploy, (
        "rolling override error must direct operators to DEPLOY_MODE=full for full runtime deploy"
    )
    assert (
        'RISK_VLLM_IMAGE_TO_DEPLOY="${RISK_VLLM_IMAGE_TO_DEPLOY:-${RISK_VLLM_IMAGE_SHA:-}}"'
        in deploy
    ), (
        "DEPLOY_MODE=full must default RISK_VLLM_IMAGE_TO_DEPLOY from RISK_VLLM_IMAGE_SHA "
        "inside deploy_gitlab_compose.sh, not only in the .gitlab-ci.yml wrapper"
    )
    assert (
        'COLBERT_KO_VLLM_IMAGE_TO_DEPLOY="${COLBERT_KO_VLLM_IMAGE_TO_DEPLOY:-${COLBERT_KO_VLLM_IMAGE_SHA:-}}"'
        in deploy
    ), (
        "DEPLOY_MODE=full must default COLBERT_KO_VLLM_IMAGE_TO_DEPLOY from "
        "COLBERT_KO_VLLM_IMAGE_SHA inside deploy_gitlab_compose.sh"
    )
    assert "DEPLOY_MODE=full requires RISK_VLLM_IMAGE_TO_DEPLOY or RISK_VLLM_IMAGE_SHA" in deploy, (
        "full deploy must fail clearly when both risk deploy ref and risk SHA ref are absent"
    )
    assert (
        "DEPLOY_MODE=full requires COLBERT_KO_VLLM_IMAGE_TO_DEPLOY or COLBERT_KO_VLLM_IMAGE_SHA"
        in deploy
    ), (
        "full deploy must fail clearly when both colbert deploy ref and colbert SHA ref are absent"
    )
    assert 'pull_preflight_image "platform" "${PLATFORM_IMAGE_TO_DEPLOY}"' in deploy, (
        "deploy_gitlab_compose.sh must preflight pull the platform image before .env mutation"
    )
    assert 'pull_preflight_image "risk-vllm-kanana" "${RISK_VLLM_IMAGE_TO_DEPLOY}"' in deploy, (
        "full deploy must preflight pull risk-vllm-kanana before .env mutation"
    )
    assert 'pull_preflight_image "colbert-ko-vllm" "${COLBERT_KO_VLLM_IMAGE_TO_DEPLOY}"' in deploy, (
        "full deploy must preflight pull colbert-ko-vllm before .env mutation"
    )
    assert "validate_colbert_ko_artifact()" in deploy, (
        "deploy_gitlab_compose.sh must validate ColBERT-ko prepared artifact during full deploy"
    )
    assert 'local model_dir="${COLBERT_KO_MODEL_DIR:-$(get_env_value COLBERT_KO_MODEL_DIR)}"' in deploy, (
        "ColBERT artifact preflight must read COLBERT_KO_MODEL_DIR from env override or remote .env"
    )
    assert 'COLBERT_KO_MODEL_DIR is empty' in deploy, (
        "ColBERT artifact preflight must fail when COLBERT_KO_MODEL_DIR is empty"
    )
    assert 'COLBERT_KO_MODEL_DIR must be absolute' in deploy, (
        "ColBERT artifact preflight must require an absolute path for production full deploy"
    )
    for required_artifact in [
        "/config.json",
        "/proj.pt",
        "/tokenizer",
        "/encoder/config.json",
        "/encoder/model.safetensors",
    ]:
        assert required_artifact in deploy, (
            f"ColBERT artifact preflight must check {required_artifact}"
        )
    assert "PREPARE_COLBERT_KO_ARTIFACT" in deploy, (
        "deploy_gitlab_compose.sh must support explicit PREPARE_COLBERT_KO_ARTIFACT opt-in"
    )
    assert "prepare_colbert_ko_vllm_artifact.py --output-dir" in deploy, (
        "PREPARE_COLBERT_KO_ARTIFACT=1 must run the existing artifact preparation script"
    )
    artifact_preflight_pos = deploy.find("validate_colbert_ko_artifact")
    backup_pos = deploy.find("cp .env")
    compose_up_pos = deploy.find('docker compose -f "${COMPOSE_FILE}" --env-file .env up -d --remove-orphans')
    assert artifact_preflight_pos != -1 and artifact_preflight_pos < backup_pos, (
        "ColBERT artifact preflight must run before .env backup/mutation"
    )
    assert artifact_preflight_pos < compose_up_pos, (
        "ColBERT artifact preflight must run before docker compose up"
    )
    assert "set_env_value COLBERT_KO_VLLM_IMAGE" in deploy, (
        "deploy_gitlab_compose.sh must update COLBERT_KO_VLLM_IMAGE in server .env"
    )
    assert ".env.bak." in deploy, (
        "deploy_gitlab_compose.sh must back up .env before modifying image refs"
    )
    assert "fail_after_env_backup()" in deploy, (
        "deploy_gitlab_compose.sh must provide a helper for explicit failures after .env mutation"
    )
    assert "[deploy] .env backup:" in deploy, (
        "deploy_gitlab_compose.sh must print the .env backup path on post-mutation failure"
    )
    assert "build-vllm-derived" in deploy, (
        "deploy_gitlab_compose.sh must reference 'build-vllm-derived' in its full deploy "
        "error hint so operators know what to run when a vLLM image pull fails"
    )

    # preflight must appear before the first .env mutation so a failed pull
    # never leaves .env in a partially-modified state
    preflight_pos = deploy.find("preflight")
    set_env_pos = deploy.find("set_env_value PLATFORM_IMAGE")
    assert preflight_pos != -1, (
        "deploy_gitlab_compose.sh must contain an image pull preflight section"
    )
    assert preflight_pos < set_env_pos, (
        "deploy_gitlab_compose.sh preflight must appear before the first set_env_value call "
        "so .env is not modified when an image does not exist"
    )

    doc = (ROOT / "docs/operations/gitlab_cicd_deployment.md").read_text(encoding="utf-8")
    assert "Build vLLM-derived images only" in doc, (
        "GitLab CI/CD docs must describe BUILD_VLLM_DERIVED=1 as a build-only flow"
    )
    assert "deploy mode는 바뀌지 않는다" in doc, (
        "GitLab CI/CD docs must say BUILD_VLLM_DERIVED=1 does not change deploy mode"
    )
    assert "pipeline을 `DEPLOY_MODE=full` 변수로 시작" in doc, (
        "GitLab CI/CD docs must describe DEPLOY_MODE=full as the full runtime deploy flow"
    )
    assert "`RISK_VLLM_IMAGE_SHA`와 `COLBERT_KO_VLLM_IMAGE_SHA`가 기본 배포 image" in doc, (
        "GitLab CI/CD docs must state full deploy defaults to current risk/colbert SHA images"
    )
    assert "BUILD_VLLM_DERIVED=1` 또는 `DEPLOY_MODE=full" in doc, (
        "GitLab CI/CD docs must mention both build-vllm-derived trigger variables"
    )
    assert "raw Hugging Face cache" in doc and "prepared artifact" in doc, (
        "GitLab CI/CD docs must distinguish raw HF cache/source from prepared ColBERT artifact directory"
    )
    assert "prepare_colbert_ko_vllm_artifact.py" in doc, (
        "GitLab CI/CD docs must include ColBERT-ko artifact preparation instructions"
    )
    assert "PREPARE_COLBERT_KO_ARTIFACT=1" in doc, (
        "GitLab CI/CD docs must document the explicit artifact preparation opt-in"
    )
    assert "COLBERT_KO_MODEL_DIR=./models/colbert-ko-vllm" in doc, (
        "GitLab CI/CD docs must preserve local init-env-compose reproducibility with repo-relative artifact path"
    )
    assert "`BUILD_VLLM_DERIVED=1`은 image build intent" in (
        ROOT / "docs/development/build_ux.md"
    ).read_text(encoding="utf-8"), (
        "build UX docs must not treat BUILD_VLLM_DERIVED=1 as equivalent to DEPLOY_MODE=full"
    )


def test_monitoring_config_includes_colbert_ko_vllm_port() -> None:
    """configs/monitoring.yaml must list port 9404 (colbert-ko-vllm) in vllm_instances."""
    monitoring = yaml.safe_load((ROOT / "configs/monitoring.yaml").read_text(encoding="utf-8"))
    ports = monitoring["metric_sources"]["vllm_instances"]["ports"]
    assert 9404 in ports, (
        "configs/monitoring.yaml vllm_instances.ports must include 9404 (colbert-ko-vllm)"
    )
