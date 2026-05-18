from __future__ import annotations

import json
import tomllib
from pathlib import Path

from scripts.models import prepare_colbert_ko_vllm_artifact as prep


ROOT = Path(__file__).resolve().parents[2]


def test_prepare_colbert_vllm_artifact_preserves_projection_and_layout(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    (snapshot / "encoder").mkdir(parents=True)
    (snapshot / "tokenizer").mkdir()
    (snapshot / "encoder" / "config.json").write_text(
        json.dumps({"architectures": ["EmbeddingGemmaModel"], "hidden_size": 768}),
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
