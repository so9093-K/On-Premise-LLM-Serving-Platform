from __future__ import annotations


def register() -> None:
    """Register the ColBERT-ko pooling model with vLLM.

    vLLM discovers this function through ``VLLM_PLUGINS`` in the dedicated
    ColBERT runtime image.  The model class is registered lazily so importing
    the plugin does not initialize torch/CUDA before vLLM forks workers.
    """
    from vllm import ModelRegistry

    ModelRegistry.register_model(
        "ColbertKoEmbeddingGemmaForTextEncoding",
        "ai_model_serving.vllm_colbert.model:ColbertKoEmbeddingGemmaForTextEncoding",
    )
