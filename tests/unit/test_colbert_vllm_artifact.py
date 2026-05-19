from __future__ import annotations

import json
import tomllib
from pathlib import Path
from importlib.util import find_spec

import pytest
import yaml

from ai_model_serving.retrieval.colbert_reference import (
    _projection_weight_and_bias,
    maxsim_score,
    maxsim_scores,
    rerank_from_scores,
)
from scripts.models import prepare_colbert_ko_vllm_artifact as prep

_TORCH_AVAILABLE = find_spec("torch") is not None
skip_no_torch = pytest.mark.skipif(not _TORCH_AVAILABLE, reason="torch not installed")


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
    assert 'attn_type = "encoder_only"' in model
    assert "named_parameters" in model
    assert "_pack_flattened_inputs" in model
    assert "input_ids.ndim == 1" in model
    assert "RuntimeError" in model
    assert "outputs.last_hidden_state.to(dtype=self.projection.weight.dtype)" in model


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


# ---------------------------------------------------------------------------
# _pack_flattened_inputs unit tests (torch required)
# ---------------------------------------------------------------------------

@skip_no_torch
def test_pack_flattened_inputs_single_sequence():
    """단일 시퀀스가 1D로 들어오면 [1, len] padded tensor를 반환한다."""
    import torch
    from ai_model_serving.vllm_colbert.model import _pack_flattened_inputs
    ids = torch.tensor([1, 2, 3, 4])
    pos = torch.tensor([0, 1, 2, 3])
    padded, mask, lengths = _pack_flattened_inputs(ids, pos, pad_token_id=0)
    assert padded.shape == (1, 4)
    assert mask.shape == (1, 4)
    assert lengths == [4]
    assert padded[0].tolist() == [1, 2, 3, 4]
    assert mask[0].tolist() == [1, 1, 1, 1]


@skip_no_torch
def test_pack_flattened_inputs_two_sequences():
    """두 시퀀스가 flattened되어 들어오면 [2, max_len] 배치로 복원된다."""
    import torch
    from ai_model_serving.vllm_colbert.model import _pack_flattened_inputs
    # seq_a = [10, 11, 12], seq_b = [20, 21]
    ids = torch.tensor([10, 11, 12, 20, 21])
    pos = torch.tensor([0, 1, 2, 0, 1])
    padded, mask, lengths = _pack_flattened_inputs(ids, pos, pad_token_id=0)
    assert padded.shape == (2, 3)
    assert mask.shape == (2, 3)
    assert lengths == [3, 2]
    assert padded[0].tolist() == [10, 11, 12]
    assert padded[1].tolist() == [20, 21, 0]   # padded
    assert mask[0].tolist() == [1, 1, 1]
    assert mask[1].tolist() == [1, 1, 0]


@skip_no_torch
def test_pack_flattened_inputs_attention_mask_shape_is_num_seqs_by_max_len():
    """attention_mask shape이 [num_seqs, max_len]인지 확인한다."""
    import torch
    from ai_model_serving.vllm_colbert.model import _pack_flattened_inputs
    ids = torch.tensor([1, 2, 0, 1, 2, 3, 4])
    pos = torch.tensor([0, 1, 2, 0, 1, 2, 3])
    padded, mask, lengths = _pack_flattened_inputs(ids, pos, pad_token_id=0)
    assert mask.shape == (2, max(lengths))
    assert mask.shape == padded.shape


@skip_no_torch
def test_pack_flattened_inputs_raises_without_positions():
    """positions가 없으면 RuntimeError를 발생시킨다."""
    import torch
    from ai_model_serving.vllm_colbert.model import _pack_flattened_inputs
    ids = torch.tensor([1, 2, 3])
    with pytest.raises(RuntimeError, match="positions"):
        _pack_flattened_inputs(ids, None, pad_token_id=0)


@skip_no_torch
def test_pack_flattened_inputs_raises_on_length_mismatch():
    """input_ids와 positions 길이가 다르면 RuntimeError를 발생시킨다."""
    import torch
    from ai_model_serving.vllm_colbert.model import _pack_flattened_inputs
    ids = torch.tensor([1, 2, 3])
    pos = torch.tensor([0, 1])
    with pytest.raises(RuntimeError, match="length mismatch"):
        _pack_flattened_inputs(ids, pos, pad_token_id=0)


@skip_no_torch
def test_pack_flattened_inputs_batch_invariance_with_mock_encoder():
    """seq_a 단독 처리 결과와 seq_a+seq_b flattened batch 안의 seq_a가 동일한지 mock encoder로 확인한다."""
    import torch
    from unittest.mock import MagicMock
    from ai_model_serving.vllm_colbert.model import _pack_flattened_inputs

    # seq_a: [10, 11, 12], seq_b: [20, 21]
    seq_a = torch.tensor([10, 11, 12])
    seq_b = torch.tensor([20, 21])
    hidden_size = 4

    def mock_encoder_output(input_ids, attention_mask):
        result = MagicMock()
        batch, seq = input_ids.shape
        hidden = input_ids.float().unsqueeze(-1).expand(batch, seq, hidden_size)
        result.last_hidden_state = hidden * attention_mask.float().unsqueeze(-1)
        return result

    # 단독 seq_a 처리
    padded_a, mask_a, lengths_a = _pack_flattened_inputs(seq_a, torch.arange(len(seq_a)), pad_token_id=0)
    out_a = mock_encoder_output(padded_a, mask_a)
    hidden_a = out_a.last_hidden_state  # [1, 3, 4]

    # flattened seq_a + seq_b 처리
    ids_ab = torch.cat([seq_a, seq_b])
    pos_ab = torch.cat([torch.arange(len(seq_a)), torch.arange(len(seq_b))])
    padded_ab, mask_ab, lengths_ab = _pack_flattened_inputs(ids_ab, pos_ab, pad_token_id=0)
    out_ab = mock_encoder_output(padded_ab, mask_ab)
    hidden_ab = out_ab.last_hidden_state  # [2, 3, 4]

    # seq_a 부분 비교 (batch 내 첫 번째 seq)
    seq_a_from_batch = hidden_ab[0, : lengths_ab[0]]  # [3, 4]
    seq_a_standalone = hidden_a[0, : lengths_a[0]]    # [3, 4]
    assert torch.allclose(seq_a_standalone, seq_a_from_batch), (
        "seq_a 단독 처리 결과와 flattened batch 안의 seq_a 결과가 다릅니다. "
        "cross-sequence attention leakage가 없으면 동일해야 합니다."
    )
