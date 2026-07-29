# vLLM unified runtime — build & activation

Single derived vLLM image used by every served model: main-LLM (26B and 12B
profiles), embedding, embedding-ko, and risk-prompt. Replaces the two former
separate images `vllm-gemma4-audio` (12B multimodal) and `risk-vllm-kanana`
(Kanana risk-prompt) — merged 2026-07-24 once both patch sets were confirmed
to touch disjoint files (see `Dockerfile` header comment).

Two independent patch sets, each a no-op for the models that don't need it:

1. **Gemma4-unified multimodal** (12B only) — media decode stack (base image의
   `libsndfile1` + soundfile + librosa + PyAV), the `vision_embedder.patch_dense` FP8
   mis-quant fix, and the audio warmup `fft_length` fix. 26B is a different
   `gemma4` architecture and never exercises this code path, so it behaves
   identically on this image.
2. **Kanana Llama head_dim guard** (risk-prompt only) — explicit Llama
   `head_dim` compatibility patch. No other served model sets an explicit
   head_dim, so this is inert elsewhere.

Both patch scripts assert on the exact upstream layout they were written
against, so a base image bump that invalidates either one fails the build
loudly instead of shipping silently broken.

## Build & activate

1. Start the `release` pipeline with **`DEPLOY_MODE=full`** (or
   `BUILD_VLLM_DERIVED=1`) — the expensive ~25 GB build is a deliberate
   operator opt-in (`test_gitlab_ci_vllm_derived_build_contract`), not
   automatic on source change.
2. **`build-vllm-derived`** builds & pushes `vllm-unified` and writes the
   immutable digest to the **`build/vllm-unified-image.env`** artifact.
3. **`deploy-gpu-175`** reads that digest, pre-pulls the image, and writes the
   same pin to `VLLM_IMAGE`, `EMBEDDING_KO_VLLM_IMAGE`, `RISK_VLLM_IMAGE`, and
   `AUDIO_VLLM_IMAGE` in the 175 `.env`. `main_model_profiles.yaml` uses the
   latter for the 12B profile override.

Manual fallback (CI unavailable):
```bash
RISK_VLLM_IMAGE='gitlab.wizvera.com:4567/acl-ai-system/acl-ai-gateway/vllm-unified:<tag>' \
make build-vllm-unified-image
docker push gitlab.wizvera.com:4567/acl-ai-system/acl-ai-gateway/vllm-unified:<tag>
```

기본 base image와 호환성 pin은 `configs/recommended_images.yaml`에서 읽는다. 검증용
base 교체가 필요한 경우에만 `RISK_VLLM_BASE_IMAGE=<immutable digest>`를 명시한다.

## Switch & validate (main-LLM profile)

`POST /admin/main-model/switch {"profile":"gemma4-12b-unified-fp8","confirm_unverified":true}`

On switch the backend runs the text canary plus media boot canaries (audio/video).
If the runtime can't decode an advertised modality, the switch fails and rolls
back — 12B never goes live half-capable.
