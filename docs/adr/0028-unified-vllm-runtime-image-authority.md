# ADR-0028: Unified vLLM runtime image authority

## Status

Accepted

## Context

26B/12B Main Model profile, embedding, embedding-ko, risk-prompt는 2026-07-24부터
`ops/images/vllm-unified/Dockerfile`이 만드는 하나의 derived vLLM artifact를 사용한다.
Gemma4 multimodal patch와 Kanana compatibility patch도 같은 image 안에서 관리되고,
각 모델은 model id, revision, runner, command, resource/capability 설정으로 동작을 구분한다.

통합 전후의 운영 상태에는 `VLLM_IMAGE`, `EMBEDDING_KO_VLLM_IMAGE`,
`RISK_VLLM_IMAGE`가 별도 persistent pin처럼 존재했다. 하나의 artifact lifecycle을 여러
runtime별 env key로 표현하면 실제 build/qualification/promotion/rollback 경계보다 더 많은
상태를 만들고 artifact provenance를 모호하게 한다.

Main Model의 `AUDIO_VLLM_IMAGE`는 일부 profile이 명시적으로 선택하는 image override이며
shared runtime pin과 다른 lifecycle 의미를 가진다.

## Decision

`VLLM_IMAGE`가 일반 vLLM runtime artifact의 단일 persistent image authority를 소유한다.
Main Model의 기본 runtime, embedding, embedding-ko, risk-prompt는 동일한 qualified artifact를
소비하며 모델별 차이는 image key가 아니라 각 runtime/model configuration이 소유한다.

`EMBEDDING_KO_VLLM_IMAGE`와 `RISK_VLLM_IMAGE`는 persistent runtime configuration에서
retired key다. 기존 `.env`에 남아 있으면 env lifecycle이 제거하며 runtime과 local build는
이 값을 image authority 또는 fallback으로 해석하지 않는다.

`AUDIO_VLLM_IMAGE`는 Main Model profile-specific image override 계약을 소유한다. profile이
별도 image를 지정하지 않으면 shared `VLLM_IMAGE`를 사용한다.

shared artifact의 deployment-time promotion authority는
`VLLM_UNIFIED_IMAGE_TO_DEPLOY`다. deployment compatibility input이 존재하더라도 persistent
runtime image authority를 새로 만들지 않는다.

향후 특정 runtime에 별도 image authority가 필요하다면 env override를 먼저 추가하지 않는다.
독립 build artifact, qualification, promotion, rollback lifecycle이 실제로 필요한 경우 그
artifact boundary와 함께 새 authority를 정의한다.

## Consequences

- 하나의 qualified vLLM artifact와 하나의 persistent authority가 대응한다.
- embedding-ko와 risk-prompt는 shared artifact를 독립적으로 pin하지 않는다.
- 모델별 runtime 설정과 runtime software artifact의 책임이 분리된다.
- Main Model profile은 실제 profile-specific artifact가 필요한 경우 `AUDIO_VLLM_IMAGE`로
  별도 override를 표현할 수 있다.
- embedding-ko 또는 risk-prompt에 독립 image lifecycle이 필요해지면 별도 artifact
  qualification/promotion/rollback 계약을 먼저 정의해야 한다.

## Operational impact

- shared vLLM artifact의 현재 persistent ref는 `VLLM_IMAGE`에서 확인한다.
- shared image promotion은 `VLLM_UNIFIED_IMAGE_TO_DEPLOY`에 immutable registry digest를
  제공한다.
- image promotion input이 없는 full deploy는 현재 persistent pin을 유지한다.
- `AUDIO_VLLM_IMAGE`는 Main Model profile override로 독립적으로 해석한다.

## Related

- ADR-0010: ColBERT 제거 및 dense Korean retrieval runtime
- ADR-0013: env lifecycle non-destructive sync
- ADR-0014: image validation policy
- ADR-0018: GPU VRAM admission and per-profile runtime image
- `ops/images/vllm-unified/README.md`
- `scripts/lib/vllm_unified_image.sh`
