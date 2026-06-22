# Audio-enabled vLLM image — build & activation runbook

This image adds the audio decode stack (`libsndfile1` + `soundfile` + `librosa`) on
top of the pinned `vllm/vllm-openai:gemma4-unified-cu129` base so the Gemma 4
**unified** family's audio tower is actually reachable. The 26B text+image profile
runs identically on it (the extra libs are unused), so it is safe as the single
runtime image for both profiles.

Audio is **inert** until this image is live AND the `gemma4-12b-unified-fp8` profile
is flipped to deploy audio. Until then:
- the gateway only accepts `audio` when the active profile's `deployed_input`
  includes it (26B never does);
- the backend audio boot canary only runs for an `audio_enabled` profile.

## 1. Build & push (CI — canonical)

The image is built by the **`build-vllm-derived`** CI job (same job as
risk-vllm-kanana, so the ~25 GB vLLM base is pulled once). Trigger a release/tag
pipeline with `BUILD_VLLM_DERIVED=1` (or `DEPLOY_MODE=full`). The job pushes
`vllm-gemma4-audio` and writes the immutable digest to the
**`build/audio-image.env`** artifact (`AUDIO_VLLM_IMAGE_DIGEST=...`).

Manual fallback (only if CI is unavailable):

```bash
docker build --build-arg BASE_IMAGE="$(yq '.runtime.image' configs/main_model_profiles.yaml)" \
  -t gitlab.wizvera.com:4567/acl-ai-system/acl-ai-gateway/vllm-gemma4-audio:<tag> \
  -f ops/images/vllm-gemma4-audio/Dockerfile .
docker push gitlab.wizvera.com:4567/acl-ai-system/acl-ai-gateway/vllm-gemma4-audio:<tag>
docker inspect --format '{{index .RepoDigests 0}}' \
  gitlab.wizvera.com:4567/acl-ai-system/acl-ai-gateway/vllm-gemma4-audio:<tag>
```

## 2. Pin it on the 12B profile only (26B untouched)

In `configs/main_model_profiles.yaml`, set the **per-profile image override** on
`gemma4-12b-unified-fp8` to the digest from step 1:

```yaml
  gemma4-12b-unified-fp8:
    image: gitlab.wizvera.com:4567/acl-ai-system/acl-ai-gateway/vllm-gemma4-audio@sha256:<digest>
```

The loader resolves this per profile (falling back to the shared `runtime.image`
for any profile without an override), so **26B keeps the plain base** and the audio
runtime travels only with the 12B profile — switching profiles carries the
capability with it. Pinning `runtime.image` (which both profiles share) is the old,
pre-override approach; do not use it.

## 3. Flip the 12B profile to deploy audio

In `configs/main_model_profiles.yaml` under `gemma4-12b-unified-fp8`:

```yaml
    capabilities:
      deployed_input: [text, image, audio]   # was [text, image]
      audio: enabled
      audio_enabled: true                     # was false
      # remove audio_block_reason
```

Also remove the two `command` lines that force the 26B chat template, so vLLM uses
the unified model's **bundled** `chat_template.jinja` (which templates audio tokens):

```yaml
    # delete these two lines from the gemma4-12b-unified-fp8 command:
    - --chat-template
    - /app/configs/gemma4_chat_template.jinja
```

The 26B profile keeps its forced template. (We intentionally do NOT change the 12B
command before activation, so a text+image-only trial switch of 12B still runs on the
same validated template as 26B.)

## 4. Switch & validate

`POST /admin/main-model/switch {"profile":"gemma4-12b-unified-fp8","confirm_unverified":true}`

On switch the backend runs the text canary **and** the audio boot canary. If the
runtime cannot decode audio the switch fails and rolls back to 26B — audio is never
half-enabled live.
