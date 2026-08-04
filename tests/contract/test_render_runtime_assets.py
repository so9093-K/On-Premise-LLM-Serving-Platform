"""render_runtime_assets.py drift 감지 및 write 정합성 테스트."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ai_model_serving.domain import ModelRegistry  # noqa: E402
from scripts.render_runtime_assets import (  # noqa: E402
    RUNTIME_TARGETS_BEGIN,
    RUNTIME_TARGETS_END,
    compare_artifact,
    compare_doc_block,
    get_artifacts,
    get_doc_patches,
    patch_doc_block,
    render_prometheus_yml,
    render_runtime_targets_block,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ── 픽스처 ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def catalog() -> dict[str, Any]:
    return _load_yaml(ROOT / "configs/model_catalog.yaml")


@pytest.fixture(scope="module")
def serving() -> dict[str, Any]:
    return _load_yaml(ROOT / "configs/model_serving.yaml")


@pytest.fixture(scope="module")
def monitoring() -> dict[str, Any]:
    return _load_yaml(ROOT / "configs/monitoring.yaml")


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    return _load_yaml(ROOT / "configs/services.yaml")["services"]


@pytest.fixture(scope="module")
def registry(catalog: dict[str, Any], serving: dict[str, Any]) -> ModelRegistry:
    return ModelRegistry(catalog, serving)




# ── 포트 변경 시 산출물 전파 ──────────────────────────────────────────────────

def test_port_change_propagates_to_prometheus(
    catalog: dict[str, Any], serving: dict[str, Any], monitoring: dict[str, Any], services: dict[str, Any]
) -> None:
    serving2 = copy.deepcopy(serving)
    serving2["models"]["main_llm"]["port"] = 9999
    serving2["models"]["main_llm"]["endpoint"] = "http://main-llm-vllm:9999/v1"
    registry2 = ModelRegistry(catalog, serving2)
    rendered = render_prometheus_yml(registry2, monitoring, services)
    assert "main-llm-vllm:9999" in rendered
    assert "main-llm-vllm:9401" not in rendered


def test_port_change_propagates_to_runtime_targets_block(
    catalog: dict[str, Any], serving: dict[str, Any]
) -> None:
    serving2 = copy.deepcopy(serving)
    serving2["models"]["main_llm"]["port"] = 9999
    serving2["models"]["main_llm"]["endpoint"] = "http://main-llm-vllm:9999/v1"
    registry2 = ModelRegistry(catalog, serving2)
    block = render_runtime_targets_block(registry2)
    assert "9999" in block
    assert "9401" not in block


# ── --check 모드 exit code 확인 ────────────────────────────────────────────────

def test_compare_artifact_detects_mismatch(tmp_path: Path) -> None:
    f = tmp_path / "test.yaml"
    f.write_text("models:\n  a: 1\n", encoding="utf-8")
    expected = "# comment\nmodels:\n  a: 2\n"
    assert not compare_artifact(f, expected)


def test_compare_artifact_yaml_ignores_header_comments(tmp_path: Path) -> None:
    f = tmp_path / "test.yaml"
    f.write_text("# old header\nmodels:\n  a: 1\n", encoding="utf-8")
    expected = "# new header\nmodels:\n  a: 1\n"
    assert compare_artifact(f, expected)


def test_compare_artifact_json_ignores_whitespace(tmp_path: Path) -> None:
    f = tmp_path / "test.json"
    f.write_text('{"a": 1}', encoding="utf-8")
    assert compare_artifact(f, '{"a":  1}\n')
    assert not compare_artifact(f, '{"a": 2}')


def test_compare_artifact_missing_file(tmp_path: Path) -> None:
    assert not compare_artifact(tmp_path / "nonexistent.yaml", "content")


# ── doc block 패치 로직 ────────────────────────────────────────────────────────

def test_patch_doc_block_replaces_existing_block() -> None:
    content = "before\n<!-- BEGIN FOO -->\nold content\n<!-- END FOO -->\nafter"
    new_block = "<!-- BEGIN FOO -->\nnew content\n<!-- END FOO -->"
    result = patch_doc_block(content, "<!-- BEGIN FOO -->", "<!-- END FOO -->", new_block)
    assert "new content" in result
    assert "old content" not in result
    assert "before" in result
    assert "after" in result


def test_patch_doc_block_raises_when_markers_absent() -> None:
    content = "before\nsome text"
    new_block = "<!-- BEGIN FOO -->\nnew\n<!-- END FOO -->"
    with pytest.raises(ValueError, match="not found"):
        patch_doc_block(content, "<!-- BEGIN FOO -->", "<!-- END FOO -->", new_block)


def test_compare_doc_block_returns_false_when_no_markers(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("no markers here", encoding="utf-8")
    assert not compare_doc_block(f, "<!-- BEGIN FOO -->", "<!-- END FOO -->", "<!-- BEGIN FOO -->\n<!-- END FOO -->")


# ── --write 후 --check clean 상태 (tmp_path isolation) ────────────────────────

def test_write_then_check_is_clean(
    tmp_path: Path, catalog: dict[str, Any], serving: dict[str, Any], monitoring: dict[str, Any], services: dict[str, Any]
) -> None:
    """tmp 디렉토리에서 --write를 흉내 낸 뒤 compare_artifact가 True를 반환하는지 확인한다."""
    registry = ModelRegistry(catalog, serving)

    artifacts = get_artifacts(registry, monitoring, services, tmp_path)
    for path, content in artifacts:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    for path, expected in artifacts:
        assert compare_artifact(path, expected), f"{path.name} should be up to date after write"
