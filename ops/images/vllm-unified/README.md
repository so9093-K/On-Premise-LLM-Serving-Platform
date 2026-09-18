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

Build the image on a native Linux amd64 Docker daemon. The output name is an
explicit build parameter and is not read from the runtime `.env`.

```bash
VLLM_UNIFIED_BUILD_IMAGE='registry.example.com/project/vllm-unified:<tag>' \
make build-vllm-unified-image
docker push registry.example.com/project/vllm-unified:<tag>
```

운영 승격은 push 결과의 `name@sha256:...` digest를 사용한다. shared runtime의
persistent image authority는 `VLLM_IMAGE`다. Main LLM의 일반 profile,
embedding, embedding-ko, risk-prompt는 이 authority가 가리키는 검증된 artifact를
공유한다.

`EMBEDDING_KO_VLLM_IMAGE`와 `RISK_VLLM_IMAGE`는 retired persistent key이며
runtime 또는 local build의 override로 해석하지 않는다. 기존 `.env`에 남아 있으면
`make sync-env`가 제거한다. Main Model profile이 명시적으로 다른 image를 선택해야 할 때는
`MAIN_MODEL_VLLM_IMAGE_OVERRIDE`가 그 profile-specific override를 소유한다.

원격 shared artifact promotion의 canonical input은 `VLLM_UNIFIED_IMAGE_TO_DEPLOY`다.
Registry publish와 원격 적용은 특정 CI provider의 책임으로 저장소에 고정하지 않는다.

기본 base image와 호환성 pin은 `configs/vllm_unified_build.yaml`에서 읽는다. 검증용
base 교체가 필요한 경우에만 그 빌드 한 번에 한정해 immutable digest를 넘긴다.

```bash
VLLM_BASE_IMAGE='vllm/vllm-openai@sha256:<digest>' make build-vllm-unified-image
```

digest가 아닌 값(태그 등)은 빌드가 거부한다. 이 키는 `.env`에서 읽지 않으며
`configs/env_contract.yaml`의 `removed_keys`에 등록되어 `make sync-env`가 제거한다 —
base를 영속 파일에 적어두면 값이 낡아도 아무도 모른 채 canonical digest를 계속
덮어쓰기 때문이다.

## Switch & validate (main-LLM profile)

`POST /admin/main-model/switch {"profile":"gemma4-12b-unified-fp8","confirm_unverified":true}`

On switch the backend runs the text canary plus media boot canaries (audio/video).
If the runtime can't decode an advertised modality, the switch fails and rolls
back — 12B never goes live half-capable.


> Compatibility: 기존 persistent `AUDIO_VLLM_IMAGE`와 deployment input
> `AUDIO_VLLM_IMAGE_TO_DEPLOY`는 migration 기간에만 읽기 alias로 허용한다.
> `make sync-env`는 persistent 값을 `MAIN_MODEL_VLLM_IMAGE_OVERRIDE`로 이동한다.
