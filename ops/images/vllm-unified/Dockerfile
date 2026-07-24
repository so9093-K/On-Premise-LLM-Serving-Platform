# Single vLLM runtime image for every model this platform serves: main-LLM
# (26B and 12B profiles), embedding, embedding-ko, and risk-prompt.
#
# Carries two independent, non-overlapping patch sets on top of the pinned
# official vLLM release:
#
#   1. Gemma4-unified multimodal fixes (12B profile only exercises these paths;
#      26B/embedding/embedding-ko/risk-prompt never touch gemma4_unified code,
#      so these are no-ops for them):
#      - media decode stack (libsndfile1 + soundfile + librosa + PyAV) for
#        audio/video input.
#      - vision_embedder.patch_dense wrongly FP8-quantized (vLLM ignore-list
#        name mismatch) -> image requests returned pad-only output.
#      - feature_extractor.fft_length missing on Gemma4UnifiedAudioFeatureExtractor
#        -> audio warmup crashed.
#      See ops/patches/apply_gemma4_multimodal_patches.py.
#
#   2. Kanana Safeguard-Prompt (risk-prompt) Llama head_dim compatibility guard
#      (only risk-prompt sets an explicit head_dim; every other served model is
#      unaffected). See ops/patches/transformers_llama_head_dim_guard.py.
#
# Both patch scripts assert on the exact upstream layout they were written
# against, so a base image bump that invalidates either one fails this build
# loudly instead of shipping silently broken. Merging them into one image is
# safe because they edit disjoint files (vllm/model_executor/models/gemma4_unified.py
# + transformers' Gemma4UnifiedAudioFeatureExtractor vs. transformers'
# configuration_llama.py) -- verified by inspection, 2026-07-24.
ARG BASE_IMAGE=vllm/vllm-openai:v0.25.1-cu129
FROM ${BASE_IMAGE}

ARG TRANSFORMERS_MIN_VERSION=4.52.4

LABEL org.opencontainers.image.title="ai-model-serving-vllm-unified"
LABEL org.opencontainers.image.description="Single vLLM runtime for all served models: Gemma4 multimodal fixes + Kanana Llama head_dim guard"
LABEL ai_model_serving.patch.gemma4_multimodal="true"
LABEL ai_model_serving.patch.transformers_llama_head_dim_guard="true"
LABEL ai_model_serving.patch.reason="Kanana explicit head_dim compatibility"
LABEL ai_model_serving.patch.remove_when="Upstream Transformers/vLLM honors explicit Llama head_dim without image patch"
LABEL ai_model_serving.patch.metadata_path="/usr/local/share/ai-model-serving/patches/transformers_llama_head_dim_guard.json"
LABEL ai_model_serving.transformers_min_version="${TRANSFORMERS_MIN_VERSION}"

# ── Gemma4-unified multimodal: media decode stack ──────────────────────────
# libsndfile1 is the native backend soundfile binds to.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir \
    "soundfile==0.12.1" \
    "librosa==0.10.2.post1" \
    "av==17.1.0"

RUN python3 -c "import av, soundfile, librosa; print('media decode ok', soundfile.__version__, librosa.__version__, av.__version__)"

# ── Gemma4-unified multimodal: image FP8 mis-quant + audio fft_length fixes ─
COPY ops/patches/apply_gemma4_multimodal_patches.py /tmp/apply_gemma4_multimodal_patches.py
RUN python3 /tmp/apply_gemma4_multimodal_patches.py \
    && rm /tmp/apply_gemma4_multimodal_patches.py

# ── Kanana risk-prompt: Transformers minimum-version floor ──────────────────
# The base image may ship a newer Transformers release, but the image must
# not fall below the known Kanana compatibility floor.
RUN python3 - <<'PY'
from __future__ import annotations

import os
import sys

import transformers

minimum = os.environ.get("TRANSFORMERS_MIN_VERSION", "4.52.4")
actual = transformers.__version__


def parse(value: str) -> tuple[int, int, int]:
    core = value.split("+", 1)[0].split("-", 1)[0]
    parts = core.split(".")[:3]
    nums = []
    for part in parts:
        digits = "".join(ch for ch in part if ch.isdigit())
        nums.append(int(digits or 0))
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)

if parse(actual) < parse(minimum):
    print(f"transformers {actual} is below Kanana minimum {minimum}", file=sys.stderr)
    sys.exit(1)
print(f"vllm-unified transformers={actual} minimum={minimum}")
PY

# ── Kanana risk-prompt: Llama head_dim guard ────────────────────────────────
COPY ops/patches/transformers_llama_head_dim_guard.py /usr/local/bin/transformers_llama_head_dim_guard.py
RUN python3 /usr/local/bin/transformers_llama_head_dim_guard.py --json \
    && python3 /usr/local/bin/transformers_llama_head_dim_guard.py --verify --json

RUN python3 - <<'PY'
import huggingface_hub
import transformers
print("vllm-unified transformers=", transformers.__version__)
print("vllm-unified huggingface_hub=", huggingface_hub.__version__)
PY
