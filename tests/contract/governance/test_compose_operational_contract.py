from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_compose_gpu_budget_uses_configured_avoid_above() -> None:
    validator = (ROOT / "scripts/compose/validate_vllm_compose.py").read_text(encoding="utf-8")
    diagnostics = (ROOT / "scripts/compose/compose_diagnostics.sh").read_text(encoding="utf-8")
    assert "GPU_BUDGETS_PATH" in validator
    assert "avoid_above" in validator
    assert "0.90" not in validator
    assert "GPU_AVOID_ABOVE" in diagnostics
    assert "0.90" not in diagnostics


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
    assert "vllm_container_cpu_cores_used" in rules
    assert "container_label_com_docker_compose_service" in rules
    assert "vllm:kv_cache_usage_perc" in rules
    assert "(vllm:kv_cache_usage_perc <= 1)" in rules
    assert "((vllm:kv_cache_usage_perc > 1) / 100)" in rules

    private_compose = (ROOT / "ops/compose/full-stack.private-network.yaml").read_text(encoding="utf-8")
    assert "dcgm-exporter:" in private_compose
    assert "cadvisor:" in private_compose
    assert (
        "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH: "
        "/var/lib/grafana/dashboards/gpu_capacity_and_oom_risk.json"
    ) in private_compose
    assert "${DCGM_EXPORTER_IMAGE:" in private_compose
    assert "env_file: ../../.env" in private_compose
    assert "copy to compose.yaml" not in private_compose.lower()
    assert "${GATEWAY_BIND_ADDR:-0.0.0.0}:${GATEWAY_PORT:-9400}:9400" in private_compose

    master_open_overlay = (ROOT / "ops/compose/overrides/exposure.master-open.yaml").read_text(encoding="utf-8")
    assert "${DCGM_EXPORTER_PORT:-9412}:9400" in master_open_overlay or "9412:9400" in master_open_overlay
    assert "${CADVISOR_PORT:-9413}:8080" in master_open_overlay or "9413:8080" in master_open_overlay

    full_stack_doc = (ROOT / "docs/operations/full_stack_runtime.md").read_text(encoding="utf-8")
    assert "docker compose -f ops/compose/full-stack.private-network.yaml --env-file .env up" in full_stack_doc
    assert "dcgm-exporter:9400" in full_stack_doc
    assert "gitlab_cicd_deployment.md" in full_stack_doc


def test_compose_env_example_has_reviewed_image_defaults() -> None:
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    env = (ROOT / '.env.compose.example').read_text(encoding='utf-8')
    assert f'PLATFORM_IMAGE=ai-model-serving-platform:{version}' in env
    assert 'VLLM_IMAGE=vllm/vllm-openai:gemma4' in env
    assert 'RISK_VLLM_IMAGE=ai-model-serving-risk-vllm-kanana:' in env
    assert 'RISK_VLLM_TRANSFORMERS_VERSION=4.52.4' in env
    assert 'EMBEDDING_KO_VLLM_IMAGE=vllm/vllm-openai:' in env
    assert 'COLBERT_KO' not in env
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
    assert "SKIP_PREFLIGHT=1" in makefile and "bash scripts/compose/compose_up.sh" in makefile
    assert "ALLOW_SKIP_PREFLIGHT" in script
    assert "CHANGE_TICKET" in script
    assert "SKIP_PREFLIGHT=1 is forbidden" in script
    assert 'scripts/env/env_validate.py --env-file "$ENV_FILE"' in script
    assert 'EXPOSURE_MODE_EFFECTIVE="$(_env_value EXPOSURE_MODE)"' in script
    assert "scripts/env/env_get.py" in script
    assert "docker compose -f" in script
    assert '--env-file "$${ENV_FILE:-.env}" config' in makefile


def test_make_compose_up_uses_env_file_as_exposure_source_of_truth() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "EXPOSURE_MODE ?= private_network" not in makefile
    assert "\ncompose-up:\n\tbash scripts/compose/compose_up.sh\n" in makefile
    assert "\npreflight-compose:\n\tbash scripts/compose/preflight_compose.sh\n" in makefile
    assert "\ncompose-config:\n\t@set -euo pipefail; \\\n" in makefile
    assert "scripts/env/env_validate.py --env-file \"$${ENV_FILE:-.env}\"" in makefile
    assert "\nexposure-status:\n\t$(PYTHON) scripts/auth/exposure_status.py $(AUTH_ENV_ARG)\n" in makefile


def test_compose_config_does_not_call_docker_when_env_file_is_invalid(tmp_path) -> None:
    env_file = tmp_path / "bad.env"
    env_file.write_text(
        "APP_ENV=production\nAUTH_MODE=strict\nEXPOSURE_MODE=private_network\nEXPOSURE_MODE=master_open\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> {docker_log}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["ENV_FILE"] = str(env_file)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    result = subprocess.run(
        ["make", "compose-config"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "duplicate env key 'EXPOSURE_MODE'" in result.stderr
    assert not docker_log.exists(), result.stdout + result.stderr


def test_prometheus_admin_token_uses_compose_secret_not_bind_mount() -> None:
    for rel in [
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


def test_production_compose_files_have_no_build_blocks() -> None:
    """Build blocks belong only in the local-build override, never in production compose."""
    for rel in [
        "ops/compose/full-stack.private-network.yaml",
    ]:
        compose = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
        for svc_name, svc in compose["services"].items():
            assert "build" not in svc, (
                f"{rel}: service '{svc_name}' must not have a 'build' block. "
                f"Use ops/compose/full-stack.local-build.yaml for local builds."
            )
