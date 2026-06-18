from __future__ import annotations

from pathlib import Path

from ai_model_serving.main_model_boot import resolve_compose_relative_path


def test_hf_cache_relative_path_uses_compose_file_directory(tmp_path):
    compose = tmp_path / "ops" / "compose" / "full-stack.yaml"
    compose.parent.mkdir(parents=True)
    compose.write_text("services: {}\n", encoding="utf-8")
    assert resolve_compose_relative_path(
        "./model_cache/huggingface", compose
    ) == compose.parent / "model_cache/huggingface"


def test_hf_cache_absolute_path_is_preserved(tmp_path):
    absolute = tmp_path / "hf-cache"
    assert resolve_compose_relative_path(str(absolute), tmp_path / "compose.yaml") == absolute
