"""HF 캐시 경로 해석(resolve_compose_relative_path)이 compose 파일 기준 상대
경로와 절대 경로를 모두 올바르게 처리하는지 검증한다."""

from __future__ import annotations

from pathlib import Path

from ai_model_serving.main_model.boot import resolve_compose_relative_path


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
