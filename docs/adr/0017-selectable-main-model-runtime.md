# ADR-0017: Selectable Gemma 4 main-model runtime

Date: 2026-06-18

## Status

Accepted

GPU switch validation remains pending until the 12B checkpoint has been
downloaded and exercised on the deployment host.

## Current-state analysis

- `main-llm-vllm` starts from
  `ops/compose/full-stack.private-network.yaml`; its 26B model command is
  currently static.
- `local-main` is the public API identity. It is projected through
  `configs/model_catalog.yaml`, `configs/model_serving.yaml`, Gateway settings,
  schemas, model cards, tests, and the vLLM `--served-model-name` argument.
- Runtime Control currently manages only embedding and risk runtimes. Gateway
  calls the internal Admin Sidecar, and only the sidecar receives the read-only
  Docker socket mount.
- Existing sidecar operations can start and stop an existing container. They
  cannot apply a different model command, so a main-model switch requires an
  allowlisted recreate operation.
- Existing runtime intent is in Gateway memory only. Main-model selection needs
  sidecar-owned persistent state because the sidecar performs and observes the
  Docker transaction.
- The current 26B command is preserved byte-for-byte as the 26B profile:
  context 20000, one sequence, 20000 batched tokens, optimization level 3,
  GPU utilization 0.76, Gemma 4 reasoning/tool parsers, project chat template,
  prefix caching, and xgrammar structured outputs.

## Verified upstream facts

- 26B checkpoint revision:
  `8edbb9269ec9c3faad538ee1208a07eb46051f34`.
- 12B checkpoint revision:
  `67e53491df7a281623fa740de61307d5c542b7f4`.
- Runtime image used by the current host:
  `vllm/vllm-openai@sha256:f4492643056969529a74238f71dd66dc3097c0d433156a4f4478456bf84bd276`.
- Google documents Gemma 4 26B A4B as text/image input and 12B Unified as
  text/image/audio input. Both generate text.
- The RedHatAI 12B FP8 card calls the checkpoint preliminary and says it was
  tested against vLLM nightly. That is not evidence that this deployment image
  supports every modality.
- vLLM documentation and Google capability documentation are not yet sufficient
  evidence for this Gateway's audio request contract. Audio therefore remains
  disabled in this change.
- The current host is an NVIDIA RTX 6000 Ada Generation with 48 GiB VRAM. The
  running 26B service is healthy. No 24 GiB compatibility claim is made.

## Decision

Add two internal profiles sharing the public alias `local-main`:

- `gemma4-26b-a4b-fp8`
- `gemma4-12b-unified-fp8`

The Admin Sidecar owns:

- the profile allowlist;
- boot-profile precedence and profile locking;
- atomic state and operation history;
- the global switch lock;
- Docker stop/remove/create/start operations;
- health, `/v1/models`, and inference-canary validation;
- rollback to the last-known-good profile.

Gateway remains the authenticated public management boundary and proxies only
profile IDs. It never accepts model IDs, images, commands, environment values,
or Compose paths from callers. Inference is fail-closed while a switch or failed
rollback leaves the main-model gate closed.

The replacement container is created from a narrowly selected subset of the
existing Compose container configuration. The image, command, labels, mounts,
network, GPU device request, and healthcheck are controlled by the profile or
copied from the already allowlisted `main-llm-vllm` container. No shell is used.

## Boot precedence

1. `MAIN_LLM_PROFILE_LOCKED=true`: configured boot profile.
2. Last successfully committed active profile.
3. `MAIN_LLM_BOOT_PROFILE`.
4. Configuration error.

The installation default remains `gemma4-26b-a4b-fp8`, preserving upgrades.

## Switch transaction

1. Validate profile and readiness policy.
2. Acquire the global operation lock and persist `preparing`.
3. Close the main-model request gate.
4. Wait the configured drain interval.
5. Stop and remove the current container.
6. Recreate it with the selected allowlisted command.
7. Start and wait for Docker/application health.
8. Verify `/v1/models` contains `local-main`.
9. Run a minimal text canary through the vLLM API.
10. Atomically commit active and last-known-good profiles and reopen the gate.
11. On post-removal failure, recreate and validate the previous profile.
12. If rollback fails, preserve a fail-closed state and report both failures.

## Compatibility and capability policy

Compatibility is reported as `verified`, `likely`, `unverified`,
`incompatible`, or `unknown`, with reasons. A static memory threshold is not a
proof of compatibility. The 26B profile records the current deployment
validation evidence for the existing 26B deployment policy; the 12B profile remains `unverified` until boot, text,
image, tool, structured-output, reasoning, streaming, Korean, and soak checks
are completed with the pinned revision and image.

Audio is recorded as a model capability for 12B but is not a deployed product
capability. Gateway audio input remains rejected by the existing bounded
text/image contract.

## Test plan

- Profile schema, revision/image pinning, duplicate-alias safety.
- Boot precedence, lock behavior, atomic persistence, corruption handling.
- Unknown/incompatible profile rejection and concurrent-switch rejection.
- Docker transaction success, validation-stage failures, rollback success, and
  rollback failure using a fake Docker backend.
- Gateway management API authentication/proxy behavior and inference gate.
- Existing contract, OpenAPI, Compose, release, and documentation checks.
- Live GPU validation is reported separately and is not inferred from unit
  tests.

## Risks

- Docker recreate is disruptive; this is a draining switch, not zero downtime.
- A process crash between old-container removal and replacement can require
  startup reconciliation.
- The 12B checkpoint and Gemma 4 runtime support are preliminary.
- The pinned image is an immutable digest, but deployment environments must
  pull or retain that digest before switching.
