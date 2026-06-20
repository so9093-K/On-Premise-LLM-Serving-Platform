"""Unit tests for the unified /admin/runtimes budget aggregation helpers."""

from __future__ import annotations

from ai_model_serving.api.routers.gateway_runtime_control import (
    _budget_fraction,
    _budget_participant,
    _main_participant,
)

BUDGET = {
    "ceiling": 0.95,
    "used": 0.925,
    "free": 0.025,
    "participants": [
        {"key": "embedding-vllm", "vram_fraction": 0.04, "active": True},
        {"key": "risk-prompt-vllm", "vram_fraction": 0.065, "active": False},
        {"key": "main-llm-vllm", "vram_fraction": 0.76, "active": True},
    ],
}
SECONDARY_CONTAINERS = {"embedding-vllm", "embedding-ko-vllm", "risk-prompt-vllm"}


def test_budget_fraction_looks_up_by_key():
    assert _budget_fraction(BUDGET, "embedding-vllm") == 0.04
    assert _budget_fraction(BUDGET, "missing") is None
    assert _budget_fraction(None, "embedding-vllm") is None


def test_budget_participant_returns_full_record():
    assert _budget_participant(BUDGET, "risk-prompt-vllm")["active"] is False


def test_main_participant_is_the_non_secondary_one():
    main = _main_participant(BUDGET, SECONDARY_CONTAINERS)
    assert main is not None
    assert main["key"] == "main-llm-vllm"
    assert main["vram_fraction"] == 0.76


def test_main_participant_none_without_budget():
    assert _main_participant(None, SECONDARY_CONTAINERS) is None
