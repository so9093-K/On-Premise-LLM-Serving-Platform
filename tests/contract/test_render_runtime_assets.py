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
    render_model_contracts_yaml,
    render_model_list_schema_json,
    render_prometheus_yml,
    render_runtime_targets_block,
    render_runtime_validation_matrix_yaml,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ── fixtures ───────────────────────────────────────────────────────────────────

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
def registry(catalog: dict[str, Any], serving: dict[str, Any]) -> ModelRegistry:
    return ModelRegistry(catalog, serving)


# ── 현재 파일 drift 없음 확인 ─────────────────────────────────────────────────

def test_prometheus_yml_up_to_date(registry: ModelRegistry, monitoring: dict[str, Any]) -> None:
    expected = render_prometheus_yml(registry, monitoring)
    assert compare_artifact(ROOT / "ops/prometheus/prometheus.yml", expected), (
        "ops/prometheus/prometheus.yml 이 drift 되었습니다. make render-runtime-assets 를 실행하세요."
    )


def test_model_contracts_up_to_date(registry: ModelRegistry) -> None:
    expected = render_model_contracts_yaml(registry)
    assert compare_artifact(ROOT / "contracts/model_contracts.yaml", expected), (
        "contracts/model_contracts.yaml 이 drift 되었습니다. make render-runtime-assets 를 실행하세요."
    )


def test_model_list_schema_up_to_date(registry: ModelRegistry) -> None:
    expected = render_model_list_schema_json(registry)
    assert compare_artifact(ROOT / "specs/schemas/model_list_response.schema.json", expected), (
        "specs/schemas/model_list_response.schema.json 이 drift 되었습니다. make render-runtime-assets 를 실행하세요."
    )


def test_runtime_validation_matrix_up_to_date(registry: ModelRegistry) -> None:
    expected = render_runtime_validation_matrix_yaml(registry)
    assert compare_artifact(ROOT / "harness/runtime_validation_matrix.yaml", expected), (
        "harness/runtime_validation_matrix.yaml 이 drift 되었습니다. make render-runtime-assets 를 실행하세요."
    )


def test_full_stack_runtime_doc_block_up_to_date(registry: ModelRegistry) -> None:
    block = render_runtime_targets_block(registry)
    assert compare_doc_block(
        ROOT / "docs/operations/full_stack_runtime.md",
        RUNTIME_TARGETS_BEGIN,
        RUNTIME_TARGETS_END,
        block,
    ), (
        "docs/operations/full_stack_runtime.md 의 generated block 이 drift 되었습니다. "
        "make render-runtime-assets 를 실행하세요."
    )


# ── 포트 변경 시 산출물 전파 ──────────────────────────────────────────────────

def test_port_change_propagates_to_prometheus(
    catalog: dict[str, Any], serving: dict[str, Any], monitoring: dict[str, Any]
) -> None:
    serving2 = copy.deepcopy(serving)
    serving2["models"]["main_llm"]["port"] = 9999
    serving2["models"]["main_llm"]["endpoint"] = "http://main-llm-vllm:9999/v1"
    registry2 = ModelRegistry(catalog, serving2)
    rendered = render_prometheus_yml(registry2, monitoring)
    assert "main-llm-vllm:9999" in rendered
    assert "main-llm-vllm:9401" not in rendered


def test_port_change_propagates_to_contracts(
    catalog: dict[str, Any], serving: dict[str, Any]
) -> None:
    catalog2 = copy.deepcopy(catalog)
    catalog2["models"]["local-main"]["runtime"]["port"] = 9999
    serving2 = copy.deepcopy(serving)
    serving2["models"]["main_llm"]["port"] = 9999
    registry2 = ModelRegistry(catalog2, serving2)
    rendered = render_model_contracts_yaml(registry2)
    doc = yaml.safe_load(rendered)
    assert doc["models"]["local-main"]["port"] == 9999


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


# ── served_model_name 변경 시 전파 ────────────────────────────────────────────

def test_served_model_name_change_in_runtime_targets(
    catalog: dict[str, Any], serving: dict[str, Any]
) -> None:
    """served_model_name은 runtime targets block에 반영된다."""
    registry = ModelRegistry(catalog, serving)
    block = render_runtime_targets_block(registry)
    assert "local-main" in block
    assert "local-embed" in block
    assert "local-embed-ko" in block
    assert "risk-prompt" in block


def test_model_list_schema_contains_public_logical_ids(registry: ModelRegistry) -> None:
    """model_list_schema는 catalog의 public logical_id를 enum으로 갖는다."""
    schema = json.loads(render_model_list_schema_json(registry))
    ids = schema["properties"]["data"]["items"]["properties"]["id"]["enum"]
    assert "local-main" in ids
    assert "local-embed" in ids
    assert "local-embed-ko" in ids
    assert "risk-prompt" in ids


# ── 새 서비스 추가 시 matrix/monitoring target 수 증가 ────────────────────────

def test_new_service_increases_matrix_runtime_services(
    catalog: dict[str, Any], serving: dict[str, Any]
) -> None:
    catalog2 = copy.deepcopy(catalog)
    serving2 = copy.deepcopy(serving)
    catalog2["models"]["local-test"] = {
        "role": "test",
        "upstream_model_id": "test/model",
        "runtime": {
            "backend": "vllm",
            "served_model_name": "local-test",
            "port": 9499,
            "endpoint": "/v1/chat/completions",
        },
        "project_runtime_policy": {},
        "gateway_listing": {"enabled": True, "capabilities": ["chat.completions"]},
        "lifecycle": {"state": "active", "exposure": "public", "owner": "platform"},
    }
    serving2["models"]["test_llm"] = {
        "name": "test/model",
        "served_model_name": "local-test",
        "backend": "vllm",
        "port": 9499,
        "endpoint": "http://test-vllm:9499/v1",
        "max_model_len": 2048,
        "max_num_seqs": 1,
        "max_num_batched_tokens": 2048,
        "gpu_memory_utilization": 0.04,
    }
    registry2 = ModelRegistry(catalog2, serving2)
    orig_registry = ModelRegistry(catalog, serving)

    matrix_doc = yaml.safe_load(render_runtime_validation_matrix_yaml(registry2))
    vllm_check = next(
        c for c in matrix_doc["validation_checks"] if c["id"] == "vllm-runtime"
    )
    assert "test_llm" in vllm_check["runtime_services"]

    assert len(registry2.runtime_validation_targets()) == len(orig_registry.runtime_validation_targets()) + 1


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
    tmp_path: Path, catalog: dict[str, Any], serving: dict[str, Any], monitoring: dict[str, Any]
) -> None:
    """tmp 디렉토리에서 --write를 흉내 낸 뒤 compare_artifact가 True를 반환하는지 확인한다."""
    registry = ModelRegistry(catalog, serving)

    artifacts = get_artifacts(registry, monitoring, tmp_path)
    for path, content in artifacts:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    for path, expected in artifacts:
        assert compare_artifact(path, expected), f"{path.name} should be up to date after write"
