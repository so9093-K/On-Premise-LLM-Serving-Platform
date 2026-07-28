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
ARG BASE_IMAGE=vllm/vllm-openai@sha256:6a090ed9d4a3739813ce355cbd63d4c34c987a25c8409796f24912ba71c2d4a4
FROM ${BASE_IMAGE}

ARG TRANSFORMERS_VERSION=5.13.1
ARG HUGGINGFACE_HUB_VERSION=1.23.0
ARG TRANSFORMERS_MIN_VERSION=4.52.4

LABEL org.opencontainers.image.title="ai-model-serving-vllm-unified"
LABEL org.opencontainers.image.description="Single vLLM runtime for all served models: Gemma4 multimodal fixes + Kanana Llama head_dim guard"
LABEL ai_model_serving.patch.gemma4_multimodal="true"
LABEL ai_model_serving.patch.transformers_llama_head_dim_guard="true"
LABEL ai_model_serving.patch.reason="Kanana explicit head_dim compatibility"
LABEL ai_model_serving.patch.remove_when="Upstream Transformers/vLLM honors explicit Llama head_dim without image patch"
LABEL ai_model_serving.patch.metadata_path="/usr/local/share/ai-model-serving/patches/transformers_llama_head_dim_guard.json"
LABEL ai_model_serving.transformers_version="${TRANSFORMERS_VERSION}"
LABEL ai_model_serving.huggingface_hub_version="${HUGGINGFACE_HUB_VERSION}"
LABEL ai_model_serving.transformers_min_version="${TRANSFORMERS_MIN_VERSION}"

# ── Gemma4-unified multimodal: media decode stack ──────────────────────────
# The pinned upstream base already includes libsndfile1, the native backend
# soundfile binds to.  Verify it locally rather than running apt-get update:
# re-fetching mutable Ubuntu/PPA metadata makes a derived-image rebuild depend
# on CI network state without changing the resulting dependency.
RUN dpkg-query -W -f='libsndfile1=${Version}\n' libsndfile1

COPY ops/images/vllm-unified/requirements.media.lock /tmp/requirements.media.lock
RUN python3 -m pip install --no-cache-dir --no-deps --requirement /tmp/requirements.media.lock \
    && rm /tmp/requirements.media.lock

RUN python3 -c "import av, soundfile, librosa; print('media decode ok', soundfile.__version__, librosa.__version__, av.__version__)"

# ── Gemma4-unified multimodal: image FP8 mis-quant + audio fft_length fixes ─
COPY ops/patches/apply_gemma4_multimodal_patches.py /tmp/apply_gemma4_multimodal_patches.py
RUN python3 /tmp/apply_gemma4_multimodal_patches.py \
    && rm /tmp/apply_gemma4_multimodal_patches.py

# ── Kanana risk-prompt: verified base dependency pair ───────────────────────
# The pinned base image is the dependency source of truth.  Do not pip-install
# over its curated vLLM stack: verify the exact known-good pair instead.
RUN python3 - "$TRANSFORMERS_VERSION" "$HUGGINGFACE_HUB_VERSION" "$TRANSFORMERS_MIN_VERSION" <<'PY'
from __future__ import annotations

import sys

import huggingface_hub
import transformers

expected_transformers, expected_hub, minimum = sys.argv[1:]
actual = transformers.__version__
actual_hub = huggingface_hub.__version__


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

if actual != expected_transformers or actual_hub != expected_hub:
    print(
        "vLLM base dependency mismatch: "
        f"transformers={actual} (expected {expected_transformers}), "
        f"huggingface_hub={actual_hub} (expected {expected_hub})",
        file=sys.stderr,
    )
    sys.exit(1)
if parse(actual) < parse(minimum):
    print(f"transformers {actual} is below Kanana floor {minimum}", file=sys.stderr)
    sys.exit(1)
print(f"vllm-unified transformers={actual} huggingface_hub={actual_hub} minimum={minimum}")
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
