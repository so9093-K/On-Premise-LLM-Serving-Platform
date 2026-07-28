from __future__ import annotations

from .helpers import *  # noqa: F401,F403

def test_gpu_resource_requirement_reference_is_packaged() -> None:
    doc = (ROOT / "docs/resources/gpu_resource_requirements_48gb.md").read_text(encoding="utf-8")
    assert "48GB VRAM 단일 GPU" in doc
    assert "0.825" in doc
    assert "runtime peak" in doc


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
        "local-embed-ko": "embedding-ko-vllm",
        "risk-prompt": "risk-prompt-vllm",
    }
    compose = yaml.safe_load((ROOT / "ops/compose/full-stack.private-network.yaml").read_text(encoding="utf-8"))
    embedding_ko_command = compose["services"]["embedding-ko-vllm"]["command"]
    assert "dragonkue/snowflake-arctic-embed-l-v2.0-ko" in embedding_ko_command
    assert "--runner" in embedding_ko_command
    assert "pooling" in embedding_ko_command
    serving = yaml.safe_load((ROOT / "configs/model_serving.yaml").read_text(encoding="utf-8"))
    assert "colbert_ko" not in serving["models"]
    risk_prompt_depends = compose["services"]["risk-prompt-vllm"]["depends_on"]
    assert risk_prompt_depends["embedding-vllm"]["condition"] == "service_healthy"
    assert risk_prompt_depends["embedding-ko-vllm"]["condition"] == "service_healthy", (
        "risk-prompt-vllm must wait for embedding-ko-vllm so vLLM runtimes start "
        "serially on a shared GPU; concurrent vLLM startup can fail memory profiling"
    )

    for path in [
        "ops/grafana/dashboards/gpu_capacity_and_oom_risk.json",
    ]:
        dashboard = json.loads((ROOT / path).read_text(encoding="utf-8"))
        assert dashboard["uid"]
        assert "contract-reference" in dashboard["tags"]
        assert dashboard["panels"]
        variable_names = {item["name"] for item in dashboard["templating"]["list"]}
        assert {"datasource", "window"}.issubset(variable_names)
        for panel in dashboard["panels"]:
            assert panel.get("datasource") == {"type": "prometheus", "uid": "${datasource}"}


def test_runtime_validation_matrix_requires_validation() -> None:
    matrix = yaml.safe_load((ROOT / "harness/runtime_validation_matrix.yaml").read_text(encoding="utf-8"))
    assert matrix["validation_policy"] == "runtime_validation_required"
    assert {check["id"] for check in matrix["validation_checks"]} >= {"gateway-runtime", "risk-adapter-runtime", "vllm-runtime", "gpu-capacity", "monitoring-scrape"}


def test_runtime_validation_matrix_has_actionable_fields() -> None:
    matrix = yaml.safe_load((ROOT / "harness/runtime_validation_matrix.yaml").read_text(encoding="utf-8"))
    for check in matrix["validation_checks"]:
        assert check["owner"]
        assert check["validation"]
        assert check["artifact_file"].startswith("reports/runtime/")
        assert check["operator_action"]
        assert check["runtime_validation_required"] is True


def test_runtime_ux_scripts_exist_and_are_executable() -> None:
    import os
    for rel in [
        'scripts/ops/start_services.sh',
        'scripts/ops/up_services.sh',
        'scripts/ops/stop_services.sh',
        'scripts/ops/down_services.sh',
        'scripts/ops/status_services.sh',
        'scripts/ops/ready_check.sh',
    ]:
        path = ROOT / rel
        assert path.exists()
        assert os.access(path, os.X_OK)


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
    # 튜닝 수치(max-model-len/max-num-batched-tokens/optimization-level/
    # gpu-memory-utilization)는 active profile이 바뀔 때마다 달라지는 값이라
    # 여기서 하드코딩하지 않는다 -- 그 정합성은
    # test_runtime_policy.py::test_main_runtime_compose_has_50k_o3_runtime_policy가
    # model_serving.yaml/catalog/model_card/compose 4곳을 직접 비교해서 이미
    # 전담한다. 이 테스트의 목적은 harness가 "패키징돼 있는지"(스크립트 존재,
    # 실행권한, 구조적으로 안정된 플래그가 렌더링되는지)를 확인하는 것이다.
    assert "--served-model-name local-main" in output
    assert "--enable-prefix-caching" in output
    assert "--prefix-caching-hash-algo sha256_cbor" in output
    assert "--enable-auto-tool-choice" in output
    assert "--tool-call-parser gemma4" in output
    assert "--reasoning-parser gemma4" in output
    assert "--chat-template" in output
    assert "--structured-outputs-config" in output


def test_compose_structured_outputs_config_matches_model_serving_yaml() -> None:
    compose = yaml.safe_load(
        (ROOT / "ops/compose/full-stack.private-network.yaml").read_text(encoding="utf-8")
    )
    model_cfg = yaml.safe_load(
        (ROOT / "configs/model_serving.yaml").read_text(encoding="utf-8")
    )
    vllm_cmd = compose["services"]["main-llm-vllm"]["command"]
    compose_idx = vllm_cmd.index("--structured-outputs-config")
    compose_config = json.loads(vllm_cmd[compose_idx + 1])

    so_cfg = model_cfg["models"]["main_llm"]["runtime_features"]["structured_outputs"]
    expected_config: dict = {
        "backend": so_cfg["backend"],
        "enable_in_reasoning": so_cfg["enable_in_reasoning"],
    }
    if so_cfg.get("disable_any_whitespace"):
        expected_config["disable_any_whitespace"] = True
    assert compose_config == expected_config, (
        f"compose --structured-outputs-config {compose_config} "
        f"does not match model_serving.yaml {expected_config}"
    )
