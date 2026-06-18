from __future__ import annotations

import json
from pathlib import Path

from scripts.models import capture_main_model_runtime_override as capture

ROOT = Path(__file__).resolve().parents[2]


def test_capture_uses_effective_project_filter_and_observed_runtime(monkeypatch):
    calls: list[list[str]] = []

    def fake_check_output(command, text):
        assert text is True
        calls.append(command)
        if command[1] == "ps":
            return "container-1\n"
        return json.dumps(
            [
                {
                    "Config": {
                        "Image": "registry.example/main@sha256:" + ("a" * 64),
                        "Cmd": ["--model", "org/previous", "--revision", "b" * 40],
                    }
                }
            ]
        )

    monkeypatch.setattr(capture.subprocess, "check_output", fake_check_output)
    document = capture.capture_runtime_override(
        catalog_path=ROOT / "configs/main_model_profiles.yaml",
        compose_project="effective-project",
    )
    assert document is not None
    assert "label=com.docker.compose.project=effective-project" in calls[0]
    service = document["services"]["main-llm-vllm"]
    assert service["image"].startswith("registry.example/main@sha256:")
    assert service["command"][1] == "org/previous"


def test_capture_allows_missing_runtime_on_initial_deployment(monkeypatch):
    monkeypatch.setattr(
        capture.subprocess,
        "check_output",
        lambda command, text: "",
    )
    assert (
        capture.capture_runtime_override(
            catalog_path=ROOT / "configs/main_model_profiles.yaml",
            compose_project="effective-project",
        )
        is None
    )
