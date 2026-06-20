# Audio-enabled vLLM runtime for the Gemma 4 unified family.
#
# The stock `vllm/vllm-openai:gemma4-unified-cu129` image ships torchaudio but NOT
# the libraries vLLM uses to decode user-supplied audio (soundfile + libsndfile,
# librosa). Without them the unified model's audio tower is unreachable: audio
# requests 400 at the runtime. This image adds exactly those decode deps on top of
# the pinned base; nothing else changes, so the 26B text+image profile behaves
# identically when run on this image.
#
# Build/push/activate is a deliberate, gated operation -- see README.md in this dir.
ARG BASE_IMAGE=vllm/vllm-openai:gemma4-unified-cu129
FROM ${BASE_IMAGE}

# libsndfile1 is the native backend soundfile binds to.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Pin the audio decode stack. librosa pulls soundfile + audioread; we name
# soundfile explicitly so the dependency is auditable.
RUN python3 -m pip install --no-cache-dir \
    "soundfile==0.12.1" \
    "librosa==0.10.2.post1"

# Fail the build if the decode stack is not importable, so a broken image never
# ships.
RUN python3 -c "import soundfile, librosa; print('audio decode ok', soundfile.__version__, librosa.__version__)"
