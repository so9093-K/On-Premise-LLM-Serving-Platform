"""Boot projection and preflight must agree without starting Docker or models."""
from __future__ import annotations

import json
import subprocess

import pytest
import yaml

from ai_model_serving.main_model.boot import render_boot_override
from scripts.compose import validate_vllm_compose as validator


def boot_config(tmp_path, *, utilization="0.76", locked=False):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "VLLM_IMAGE=registry.example.com/runtime@sha256:" + "a" * 64 + "\n"
        "MAIN_LLM_BOOT_PROFILE=gemma4-12b-unified-fp8\n"
        f"MAIN_LLM_PROFILE_LOCKED={str(locked).lower()}\n"
        f"MAIN_LLM_GPU_MEMORY_UTILIZATION={utilization}\n",
        encoding="utf-8",
    )
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "schema_version": 1, "active_profile": "gemma4-26b-a4b-fp8",
    }), encoding="utf-8")
    profile_id, boot = render_boot_override(
        catalog_path=validator.MAIN_MODEL_PROFILES_PATH,
        state_path=state_path,
        env_path=env_path,
    )
    effective = validator.load_yaml(validator.COMPOSE_PATH)
    for service_name, projection in boot["services"].items():
        effective["services"][service_name].update(projection)
    return profile_id, boot, effective


@pytest.mark.parametrize("locked", [False, True])
def test_effective_main_uses_persisted_or_locked_profile(tmp_path, locked):
    profile_id, boot, effective = boot_config(tmp_path, locked=locked)
    assert profile_id == ("gemma4-12b-unified-fp8" if locked else "gemma4-26b-a4b-fp8")
    validator.validate_alignment(effective_compose=effective, boot_override=boot)


@pytest.mark.parametrize("utilization", ["0.70", "0.80"])
def test_main_host_override_is_counted_in_total_budget(tmp_path, utilization):
    _, boot, effective = boot_config(tmp_path, utilization=utilization)
    if utilization == "0.80":
        with pytest.raises(SystemExit, match="total configured gpu_memory_utilization"):
            validator.validate_alignment(effective_compose=effective, boot_override=boot)
    else:
        validator.validate_alignment(effective_compose=effective, boot_override=boot)


@pytest.mark.parametrize("field", ["image", "command"])
def test_effective_main_must_match_exact_boot_projection(tmp_path, field):
    _, boot, effective = boot_config(tmp_path)
    service = effective["services"]["main-llm-vllm"]
    if field == "image":
        service[field] = "registry.example.com/runtime@sha256:" + "b" * 64
    else:
        service[field] = [*service[field], "--enforce-eager"]
    with pytest.raises(SystemExit, match=f"effective {field} does not match"):
        validator.validate_alignment(effective_compose=effective, boot_override=boot)


def test_auxiliary_gpu_budget_must_match_registry(tmp_path):
    _, boot, effective = boot_config(tmp_path)
    command = effective["services"]["embedding-ko-vllm"]["command"]
    command[command.index("--gpu-memory-utilization") + 1] = "0.03"
    with pytest.raises(SystemExit, match="does not match ModelRegistry projection"):
        validator.validate_alignment(effective_compose=effective, boot_override=boot)


def test_boot_override_requires_effective_config(tmp_path):
    _, boot, _ = boot_config(tmp_path)
    with pytest.raises(SystemExit, match="requires effective Compose config"):
        validator.validate_alignment(boot_override=boot)


@pytest.mark.parametrize("with_exposure", [False, True])
def test_preflight_passes_same_boot_file_to_compose_and_validator(tmp_path, monkeypatch, with_exposure):
    from scripts.compose import preflight_compose as preflight

    _, boot, effective = boot_config(tmp_path)
    boot_path = tmp_path / "boot.yaml"
    boot_path.write_text(yaml.safe_dump(boot), encoding="utf-8")
    token = tmp_path / ".runtime/prometheus/admin_api_key"
    token.parent.mkdir(parents=True)
    token.write_text("test-only", encoding="utf-8")
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    monkeypatch.setenv("COMPOSE_FILE", str(validator.COMPOSE_PATH))
    monkeypatch.setenv("ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "preflight-test")
    monkeypatch.setenv("COMPOSE_SERVICE_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("SKIP_RISK_VLLM_IMAGE_CONFIG_CHECK", "1")
    monkeypatch.setattr(preflight, "_env_value", lambda key, default="": {
        "HF_CACHE_DIR": str(tmp_path / "cache"),
    }.get(key, default))
    exposure_path = tmp_path / "exposure.yaml"
    exposure_path.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(preflight, "override_file_for", lambda _: str(exposure_path) if with_exposure else "")
    monkeypatch.setattr(preflight, "_docker_compose_available", lambda: True)
    monkeypatch.setattr(preflight, "_show_gpu", lambda: None)
    monkeypatch.setattr(preflight, "_port_available", lambda *_: True)
    commands = []
    validated = []

    def validate(compose_path, *, effective_compose, boot_override):
        assert compose_path == validator.COMPOSE_PATH
        assert boot_override == boot
        validated.append(effective_compose)
        validator.validate_alignment(compose_path, effective_compose=effective_compose, boot_override=boot_override)

    def run_status(command, *, capture=False):
        commands.append(command)
        if command[0] == "docker" and "config" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps(effective), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(preflight, "_run_status", run_status)
    monkeypatch.setattr(preflight, "validate_alignment", validate)
    assert preflight._phase2("private_network", {}, boot_override=boot_path) == 0
    compose = next(cmd for cmd in commands if cmd[0] == "docker" and "config" in cmd)
    files = [compose[index + 1] for index, arg in enumerate(compose) if arg == "-f"]
    assert files == [str(validator.COMPOSE_PATH), *([str(exposure_path)] if with_exposure else []), str(boot_path)]
    assert validated == [effective]


@pytest.mark.parametrize("supplied", [False, True])
def test_preflight_reuses_supplied_boot_or_generates_temporary_one(tmp_path, monkeypatch, supplied):
    from scripts.compose import preflight_compose as preflight

    _, boot, _ = boot_config(tmp_path)
    monkeypatch.setattr(preflight, "_phase0", lambda: {})
    monkeypatch.setattr(preflight, "_phase1", lambda _: "private_network")
    generated = []

    def render(**kwargs):
        generated.append(kwargs)
        return "gemma4-26b-a4b-fp8", boot

    monkeypatch.setattr(preflight, "render_boot_override", render)
    observed = []

    def phase2(mode, data, *, boot_override):
        assert validator.load_yaml(boot_override) == boot
        observed.append(boot_override)
        return 0

    monkeypatch.setattr(preflight, "_phase2", phase2)
    supplied_path = tmp_path / "supplied.yaml"
    supplied_path.write_text(yaml.safe_dump(boot), encoding="utf-8")
    assert preflight.main(["--boot-override", str(supplied_path)] if supplied else []) == 0
    assert bool(generated) is not supplied
    assert observed[0].exists() is supplied
    assert supplied_path.is_file()
