from pathlib import Path
import shutil

import pytest
import yaml
from ai_model_serving.runtime_topology import load_runtime_topology


def _copy_runtime_configs(destination: Path) -> Path:
    root = Path(__file__).resolve().parents[2]
    (destination / "configs").mkdir()
    for filename in ("model_serving.yaml", "services.yaml", "runtime_topology.yaml"):
        shutil.copy(root / "configs" / filename, destination / "configs" / filename)
    return destination / "configs/runtime_topology.yaml"


def test_runtime_topology_uses_explicit_lifecycle_bindings() -> None:
    root = Path(__file__).resolve().parents[2]
    topology = load_runtime_topology(root)

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
    assert topology.start_prerequisites_by_service == {
        "embedding-ko-vllm": ["embedding-vllm"],
        "risk-prompt-vllm": ["embedding-vllm", "embedding-ko-vllm"],
    }


def test_runtime_topology_does_not_derive_policy_from_compose(tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text(
        """
services:
  embedding-ko-vllm:
    depends_on:
      risk-prompt-vllm:
        condition: service_healthy
""".strip()
        + "\n",
        encoding="utf-8",
    )

    topology = load_runtime_topology(root, compose_path=compose_path)

    assert topology.start_prerequisites_by_service["embedding-ko-vllm"] == [
        "embedding-vllm"
    ]


def test_runtime_topology_rejects_wrong_service_reference(tmp_path) -> None:
    path = _copy_runtime_configs(tmp_path)
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
        (
            {"start_prerequisites": ["missing"]},
            "references unknown start prerequisite",
        ),
        (
            {"start_prerequisites": ["embedding"]},
            "cannot depend on itself",
        ),
        (
            {"start_prerequisites": ["embedding_ko", "embedding_ko"]},
            "must not contain duplicates",
        ),
    ],
)
def test_runtime_topology_rejects_invalid_binding(tmp_path, update, message) -> None:
    path = _copy_runtime_configs(tmp_path)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["runtimes"]["embedding"].update(update)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_runtime_topology(tmp_path)


def test_runtime_topology_rejects_prerequisite_cycle(tmp_path) -> None:
    path = _copy_runtime_configs(tmp_path)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["runtimes"]["embedding"]["start_prerequisites"] = ["risk_prompt"]
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match="start_prerequisites contain a cycle"):
        load_runtime_topology(tmp_path)
