from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yaml

from ai_model_serving.retrieval.colbert_reference import (
    _projection_weight_and_bias,
    maxsim_score,
    maxsim_scores,
    rerank_from_scores,
)
from scripts.models import prepare_colbert_ko_vllm_artifact as prep


ROOT = Path(__file__).resolve().parents[2]


def test_prepare_colbert_vllm_artifact_preserves_projection_and_layout(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    (snapshot / "encoder").mkdir(parents=True)
    (snapshot / "tokenizer").mkdir()
    (snapshot / "encoder" / "config.json").write_text(
        json.dumps({"architectures": ["Gemma3TextModel"], "model_type": "gemma3_text", "hidden_size": 768}),
        encoding="utf-8",
    )
    (snapshot / "encoder" / "model.safetensors").write_bytes(b"weights")
    (snapshot / "tokenizer" / "tokenizer.json").write_text("{}", encoding="utf-8")
    (snapshot / "tokenizer" / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (snapshot / "proj.pt").write_bytes(b"projection")

    monkeypatch.setattr(prep, "_import_hf", lambda: (lambda **_: str(snapshot)))
    output = prep.prepare_artifact("example/colbert", tmp_path / "out", "rev1")

    config = json.loads((output / "config.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert config["architectures"] == ["ColbertKoEmbeddingGemmaForTextEncoding"]
    assert config["model_type"] == "gemma3_text"
    assert config["projection_filename"] == "proj.pt"
    assert config["pooling_task"] == "token_embed"
    assert manifest["projection_required"] is True
    assert (output / "encoder" / "model.safetensors").exists()
    assert (output / "tokenizer" / "tokenizer.json").exists()
    assert (output / "proj.pt").exists()


def test_colbert_vllm_image_uses_named_vllm_plugin_entrypoint():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugins = pyproject["project"]["entry-points"]["vllm.general_plugins"]
    assert plugins["colbert_ko_vllm"] == "ai_model_serving.vllm_colbert.plugin:register"

    dockerfile = (ROOT / "ops/docker/Dockerfile.colbert-ko-vllm").read_text(encoding="utf-8")
    assert "VLLM_PLUGINS=colbert_ko_vllm" in dockerfile


def test_colbert_vllm_model_uses_version_compatible_pooler_factory():
    model = (ROOT / "src/ai_model_serving/vllm_colbert/model.py").read_text(encoding="utf-8")
    assert "DispatchPooler.for_embedding(pooler_config)" in model
    assert "hasattr(DispatchPooler, \"for_embedding\")" in model
    assert "Pooler.for_token_embed(pooler_config)" in model


def test_colbert_license_and_live_parity_smoke_are_declared():
    catalog = yaml.safe_load((ROOT / "configs/model_catalog.yaml").read_text(encoding="utf-8"))
    colbert_catalog = catalog["models"]["local-colbert-ko"]
    card = json.loads((ROOT / "model_cards/local-colbert-ko.json").read_text(encoding="utf-8"))
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts/validation/colbert_parity_smoke.py").read_text(encoding="utf-8")

    assert colbert_catalog["license"] == "apache-2.0"
    assert colbert_catalog["source_facts"]["license"] == "apache-2.0"
    assert colbert_catalog["source_facts"]["base_model"] == "google/embeddinggemma-300m"
    assert colbert_catalog["source_facts"]["base_model_license"] == "gemma"
    assert card["license"] == "apache-2.0"
    assert card["source_facts"]["license"] == "apache-2.0"
    assert card["source_facts"]["base_model"] == "google/embeddinggemma-300m"
    assert card["source_facts"]["base_model_license"] == "gemma"
    assert "colbert-parity-smoke:" in makefile
    assert "/score" in smoke
    assert '"text_1"' in smoke
    assert '"text_2"' in smoke
    assert "/pooling" in smoke
    assert "top-1 mismatch" in smoke


def test_reference_maxsim_fixture_ranks_expected_top1():
    query_embeddings = [[1.0, 0.0], [0.0, 1.0]]
    documents = ["weak mixed evidence", "exact two-token match", "masked distractor"]
    documents_embeddings = [
        [[0.2, 0.0], [0.0, 0.2]],
        [[1.0, 0.0], [0.0, 1.0]],
        [[10.0, 10.0], [0.0, 0.1]],
    ]
    documents_mask = [
        [True, True],
        [True, True],
        [False, True],
    ]

    scores = maxsim_scores(
        query_embeddings,
        documents_embeddings,
        query_mask=[True, True],
        documents_mask=documents_mask,
    )
    ranking = rerank_from_scores(documents, scores)

    assert scores == pytest.approx([0.4, 2.0, 0.1])
    assert ranking[0]["index"] == 1
    assert ranking[0]["document"] == "exact two-token match"
    assert maxsim_score(query_embeddings, documents_embeddings[2], document_mask=documents_mask[2]) == pytest.approx(0.1)


class _FakeTensor:
    def __init__(self, shape):
        self.shape = shape
        self.ndim = len(shape)


def test_reference_projection_fixture_requires_128_dim_proj_head():
    weight, bias = _projection_weight_and_bias(
        {
            "state_dict": {
                "linear.weight": _FakeTensor((128, 768)),
                "linear.bias": _FakeTensor((128,)),
            }
        },
        projection_dim=128,
    )
    assert weight.shape == (128, 768)
    assert bias.shape == (128,)

    with pytest.raises(RuntimeError, match="Projection dimension mismatch"):
        _projection_weight_and_bias({"linear.weight": _FakeTensor((256, 768))}, projection_dim=128)
