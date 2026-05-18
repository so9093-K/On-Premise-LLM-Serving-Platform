#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


DEFAULT_MODEL_ID = "sigridjineth/colbert-ko-embeddinggemma-300m"
DEFAULT_OUTPUT = Path("models/colbert-ko-vllm")


def _import_hf() -> Any:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: huggingface_hub. Install it in the environment used "
            "to prepare the ColBERT-ko vLLM artifact."
        ) from exc
    return snapshot_download


def _copytree_contents(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)


def prepare_artifact(model_id: str, output_dir: Path, revision: str | None = None) -> Path:
    snapshot_download = _import_hf()
    snapshot = Path(
        snapshot_download(
            repo_id=model_id,
            revision=revision,
            allow_patterns=[
                "encoder/*",
                "tokenizer/*",
                "proj.pt",
                "inference.py",
            ],
        )
    )
    required = [
        snapshot / "encoder" / "config.json",
        snapshot / "encoder" / "model.safetensors",
        snapshot / "tokenizer" / "tokenizer.json",
        snapshot / "tokenizer" / "tokenizer_config.json",
        snapshot / "proj.pt",
    ]
    missing = [str(path.relative_to(snapshot)) for path in required if not path.exists()]
    if missing:
        raise SystemExit("ColBERT-ko snapshot is missing required artifact(s): " + ", ".join(missing))

    output_dir.mkdir(parents=True, exist_ok=True)
    _copytree_contents(snapshot / "encoder", output_dir / "encoder")
    _copytree_contents(snapshot / "tokenizer", output_dir / "tokenizer")
    shutil.copy2(snapshot / "proj.pt", output_dir / "proj.pt")
    if (snapshot / "inference.py").exists():
        shutil.copy2(snapshot / "inference.py", output_dir / "reference_inference.py")

    encoder_config = json.loads((snapshot / "encoder" / "config.json").read_text(encoding="utf-8"))
    config = dict(encoder_config)
    config.update(
        {
            "architectures": ["ColbertKoEmbeddingGemmaForTextEncoding"],
            "source_model_id": model_id,
            "source_revision": revision or "default",
            "encoder_subfolder": "encoder",
            "tokenizer_subfolder": "tokenizer",
            "projection_filename": "proj.pt",
            "projection_dim": 128,
            "normalize_embeddings": True,
            "score_type": "late-interaction",
            "pooling_task": "token_embed",
            "production_vllm_native": True,
        }
    )
    (output_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "model_id": model_id,
        "revision": revision or "default",
        "layout": ["config.json", "encoder/", "tokenizer/", "proj.pt"],
        "projection_required": True,
        "vllm_architecture": "ColbertKoEmbeddingGemmaForTextEncoding",
        "score_mode": "late_interaction_maxsim",
    }
    (output_dir / "artifact_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare local-colbert-ko vLLM native artifact.")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--revision", default=None)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    path = prepare_artifact(args.model_id, Path(args.output_dir), args.revision)
    print(f"prepared ColBERT-ko vLLM artifact: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
