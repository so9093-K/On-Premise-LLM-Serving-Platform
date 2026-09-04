from pathlib import Path
import shutil

import pytest
import yaml
from ai_model_serving.runtime_topology import load_runtime_topology


def test_runtime_topology_uses_explicit_lifecycle_bindings() -> None:
    root = Path(__file__).resolve().parents[2]
    topology = load_runtime_topology(
        root,
        compose_path=root / "ops/compose/full-stack.private-network.yaml",
    )

    assert topology.service_by_key == {
        "embedding": "embedding-vllm",
        "embedding_ko": "embedding-ko-vllm",
        "risk_prompt": "risk-prompt-vllm",
    }
    assert "main_llm" not in topology.controllable_keys
    assert topology.bindings_by_key["embedding"].service_id == "embedding_vllm"
    assert topology.bindings_by_key["embedding"].compose_service == "embedding-vllm"
    assert topology.runtime_keys_for_features(frozenset({"chat"})) == frozenset({"main_llm"})
    assert topology.required_keys_for_features(
        frozenset({"chat", "embeddings", "risk"})
    ) == frozenset({"main_llm", "embedding", "embedding_ko", "risk_prompt"})


def test_runtime_topology_rejects_wrong_service_reference(tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "configs").mkdir()
    for filename in ("model_serving.yaml", "services.yaml", "runtime_topology.yaml"):
        shutil.copy(root / "configs" / filename, tmp_path / "configs" / filename)
    path = tmp_path / "configs/runtime_topology.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["runtimes"]["embedding"]["service_id"] = "risk_prompt_vllm"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match="port does not match"):
        load_runtime_topology(tmp_path)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"features": ["embeddings_typo"]}, "references unknown features"),
        (
            {"enabled": False, "required": True, "controllable": False},
            "disabled runtime topology binding.*cannot be required",
        ),
    ],
)
def test_runtime_topology_rejects_invalid_binding(tmp_path, update, message) -> None:
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "configs").mkdir()
    for filename in ("model_serving.yaml", "services.yaml", "runtime_topology.yaml"):
        shutil.copy(root / "configs" / filename, tmp_path / "configs" / filename)
    path = tmp_path / "configs/runtime_topology.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["runtimes"]["embedding"].update(update)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_runtime_topology(tmp_path)
