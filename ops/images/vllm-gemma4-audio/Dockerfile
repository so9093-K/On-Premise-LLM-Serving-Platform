# Multimodal-enabling vLLM runtime for the Gemma 4 *unified* family (12B).
#
# The stock `vllm/vllm-openai:v0.25.1-cu129` base cannot actually serve the
# 12B unified model's image OR audio input; on it `gemma4-12b-unified-fp8` serves
# text correctly but image requests return pad-only output and audio warmup crashes.
# This image fixes all three blockers on top of the pinned base:
#   1. multimodal decode stack (libsndfile1 + soundfile + librosa + PyAV) — base
#      lacks the audio/container decode path, so audio/video requests 400 at the runtime;
#   2. image — vision projection is wrongly FP8-quantized (see patch script);
#   3. audio warmup — missing `fft_length` crashes multimodal warmup (see patch script).
# Fixes 2+3 are upstream vLLM/transformers bugs; the patch script asserts on the
# upstream layout so a base bump fails the build instead of shipping broken.
#
# The patches touch only the gemma4_unified path and the decode libs are otherwise
# unused, so 26B (a different gemma4 architecture, text+image) behaves identically on
# this image — safe as a per-profile override. Build/push/activate is gated; see README.md.
ARG BASE_IMAGE=vllm/vllm-openai:v0.25.1-cu129
FROM ${BASE_IMAGE}

# libsndfile1 is the native backend soundfile binds to.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Pin the audio/container decode stack. vLLM uses PyAV/FFmpeg-backed media
# loaders for container audio/video paths, while soundfile covers simple audio
# streams. We name all direct pieces so the dependency surface is auditable.
RUN python3 -m pip install --no-cache-dir \
    "soundfile==0.12.1" \
    "librosa==0.10.2.post1" \
    "av==17.1.0"

# Fail the build if the decode stack is not importable, so a broken image never
# ships.
RUN python3 -c "import av, soundfile, librosa; print('media decode ok', soundfile.__version__, librosa.__version__, av.__version__)"

# Carry the two vLLM/transformers multimodal fixes (image FP8 mis-quant + audio
# warmup fft_length). The script asserts on the upstream code layout, so a base
# image bump that invalidates a patch fails the build loudly. Idempotent.
# Patch lives in ops/patches/ (the build-context allowlist in .dockerignore), like
# the risk image's transformers patch.
COPY ops/patches/apply_gemma4_multimodal_patches.py /tmp/apply_gemma4_multimodal_patches.py
RUN python3 /tmp/apply_gemma4_multimodal_patches.py \
    && rm /tmp/apply_gemma4_multimodal_patches.py
