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

## 1. Build & push

```bash
docker build \
  -t gitlab.wizvera.com:4567/acl-ai-system/acl-ai-gateway/vllm-gemma4-audio:<tag> \
  ops/images/vllm-gemma4-audio
docker push gitlab.wizvera.com:4567/acl-ai-system/acl-ai-gateway/vllm-gemma4-audio:<tag>
# capture the pushed digest:
docker inspect --format '{{index .RepoDigests 0}}' \
  gitlab.wizvera.com:4567/acl-ai-system/acl-ai-gateway/vllm-gemma4-audio:<tag>
```

## 2. Point the runtime at it (both refs — they are separate sources of truth)

- `.env` on the deploy host: `VLLM_IMAGE=...vllm-gemma4-audio:<tag>` (compose static
  main-llm-vllm).
- `configs/main_model_profiles.yaml` `runtime.image:` → the **sha256 digest** from
  step 1 (the hot-swap backend uses this when recreating the container).

Deploy. This recreates `main-llm-vllm` once on the new image; 26B behavior is
unchanged.

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
