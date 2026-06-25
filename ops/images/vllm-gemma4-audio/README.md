# Gemma 4 unified 12B multimodal — build & activation runbook

`gemma4-12b-unified-fp8` cannot serve image **or** audio on the stock
`vllm/vllm-openai:gemma4-unified-cu129` base — only text works there. This derived
image fixes all three blockers (each reproduced and verified live on 2026-06-25):

1. **audio I/O** — the base lacks the decode stack (`libsndfile1` + `soundfile` +
   `librosa`), so audio requests 400 at the runtime.
2. **image** (vLLM bug) — the vision projection `vision_embedder.patch_dense` is in
   the checkpoint's FP8 `ignore` list under its HF name
   (`model.vision_embedder.patch_dense`), but vLLM matches the bare internal name
   (`vision_embedder.patch_dense`), so it is wrongly FP8-quantized → image requests
   return pad-only output. Fixed by building `patch_dense` unquantized.
3. **audio warmup** (vLLM/transformers mismatch) — vLLM reads
   `feature_extractor.fft_length`, absent on transformers' `Gemma4UnifiedAudioFeatureExtractor`,
   crashing the multimodal warmup. Fixed by defining it.

Fixes 2+3 are **upstream** bugs carried by `apply_gemma4_multimodal_patches.py` (it
asserts on the upstream layout, so a base bump fails the build) — please report them
upstream so the patches can be dropped. The patches touch only the `gemma4_unified`
code path and the decode libs are otherwise unused, so 26B (a different `gemma4`
architecture) behaves identically on this image — it is safe as a per-profile override.

Activation needs **no digest pin and no config flip** — the 12B profile already declares
`image: ${AUDIO_VLLM_IMAGE}` and full `deployed_input: [text, image, audio]`. The one
variable you set is `DEPLOY_MODE=full`, which triggers the build. That trigger is a
**deliberate, governance-enforced opt-in** (`test_gitlab_ci_vllm_derived_build_contract`):
the expensive ~25 GB derived-image build runs only when an operator explicitly asks for
it. The **audio boot canary** gates go-live (decode the runtime or roll back). 26B keeps
the plain base.

## 1. Trigger with `DEPLOY_MODE=full`, then click deploy

1. Start the `release` pipeline with **`DEPLOY_MODE=full`** (or `BUILD_VLLM_DERIVED=1`).
   This is the deliberate opt-in for the expensive derived-image build.
2. **`build-vllm-derived`** builds & pushes `vllm-gemma4-audio` (shared ~25 GB base pulled
   once) and writes the immutable digest to the **`build/audio-image.env`** artifact.
3. **`deploy-gpu-175`** (manual click — the deliberate human gate, since it swaps a live
   model) auto-detects **full** because `configs/main_model_profiles.yaml` is a
   runtime-sensitive file, reads the digest artifact, pre-pulls the image on the box, and
   sets `AUDIO_VLLM_IMAGE=<digest>` in the 175 `.env`. The sidecar's loader expands
   `${AUDIO_VLLM_IMAGE}` on the 12B profile → the fixed runtime is pinned.

`AUDIO_VLLM_IMAGE` persists in `.env`, so **routine deploys reuse the pin** (no rebuild
needed). When `AUDIO_VLLM_IMAGE` is unset (image never built), the loader falls back to
the shared base and the audio canary keeps 12B from going live.

> Why manual, not auto-build-on-change? It is a **deliberate policy**, enforced by
> `test_gitlab_ci_vllm_derived_build_contract`, which requires the `BUILD_VLLM_DERIVED` /
> `DEPLOY_MODE=full` trigger (and GitLab 12.1.1 lacks `rules:` for change-detection
> anyway). Forgetting to opt in does **not** silently ship a stale image: the deploy
> guard (in `deploy_gitlab_compose.sh`) fails loudly when the image source changed but no
> rebuild ran. Auto-building on source change would mean changing that governance
> contract — a team decision, not a CI tweak.

Manual fallback (CI unavailable): build locally, then set the digest in the 175
`.env` by hand —
```bash
docker build --build-arg BASE_IMAGE="$(yq '.runtime.image' configs/main_model_profiles.yaml)" \
  -t gitlab.wizvera.com:4567/acl-ai-system/acl-ai-gateway/vllm-gemma4-audio:<tag> \
  -f ops/images/vllm-gemma4-audio/Dockerfile .
docker push  gitlab.wizvera.com:4567/acl-ai-system/acl-ai-gateway/vllm-gemma4-audio:<tag>
# AUDIO_VLLM_IMAGE=<the @sha256: digest of the pushed image>
```

## 2. Switch & validate

`POST /admin/main-model/switch {"profile":"gemma4-12b-unified-fp8","confirm_unverified":true}`

On switch the backend runs the text canary **and** the audio boot canary (it fires
for any `audio_enabled` profile). If the runtime cannot decode audio — e.g.
`AUDIO_VLLM_IMAGE` was unset so 12B fell back to the base — the switch fails and
rolls back to 26B, so 12B never goes live half-capable. The 26B chat template is kept
forced on the 12B `command`: `/app/configs/gemma4_chat_template.jinja` templates both
`<|image|>` and `<|audio|>` correctly for the unified model (token ids match the
config), so no template change is needed.
