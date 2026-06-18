from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _compose_config(*files: str) -> dict:
    command = ["docker", "compose"]
    for file in files:
        command += ["-f", file]
    command += ["--env-file", ".env", "config", "--format", "json"]
    return json.loads(subprocess.check_output(command, cwd=ROOT, text=True))


def test_private_network_does_not_publish_vllm_or_sidecar_ports():
    config = _compose_config("ops/compose/full-stack.private-network.yaml")
    services = config["services"]
    for name in (
        "main-llm-vllm",
        "embedding-vllm",
        "embedding-ko-vllm",
        "risk-prompt-vllm",
        "admin-sidecar",
    ):
        assert not services[name].get("ports"), name


def test_master_open_publishes_vllm_but_not_sidecar():
    config = _compose_config(
        "ops/compose/full-stack.private-network.yaml",
        "ops/compose/overrides/exposure.master-open.yaml",
    )
    services = config["services"]
    expected = {
        "main-llm-vllm": 9401,
        "embedding-vllm": 9402,
        "risk-prompt-vllm": 9403,
        "embedding-ko-vllm": 9406,
    }
    for name, published in expected.items():
        assert any(int(port["published"]) == published for port in services[name]["ports"])
    assert not services["admin-sidecar"].get("ports")


def test_main_model_profiles_pin_revision_image_and_26b_golden_command():
    profiles = yaml.safe_load(
        (ROOT / "configs/main_model_profiles.yaml").read_text(encoding="utf-8")
    )
    assert profiles["runtime"]["image"].startswith("vllm/vllm-openai@sha256:")
    for profile in profiles["profiles"].values():
        assert len(profile["revision"]) == 40
        assert profile["command"][profile["command"].index("--revision") + 1] == profile["revision"]

    compose = yaml.safe_load(
        (ROOT / "ops/compose/full-stack.private-network.yaml").read_text(encoding="utf-8")
    )
    assert compose["services"]["main-llm-vllm"]["command"] == profiles["profiles"][
        "gemma4-26b-a4b-fp8"
    ]["command"]


def test_ci_deploys_gateway_and_sidecar_together_and_exports_image_digest():
    pipeline = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts/ci/deploy_gitlab_compose.sh").read_text(encoding="utf-8")
    assert "PLATFORM_IMAGE_DIGEST" in pipeline
    assert "admin-sidecar" in deploy
    assert "compose_run up -d --no-deps admin-sidecar" in deploy


def test_main_model_dashboard_is_registered_and_uses_bounded_labels():
    dashboard = json.loads(
        (ROOT / "ops/grafana/dashboards/main_model_control.json").read_text(
            encoding="utf-8"
        )
    )
    text = json.dumps(dashboard)
    assert dashboard["uid"] == "main_model_control"
    for metric in (
        "main_model_profile_info",
        "main_model_gate_open",
        "main_model_switch_operations",
        "main_model_operation_state",
    ):
        assert metric in text
    assert "operation_id" not in text
    assert "error_message" not in text
