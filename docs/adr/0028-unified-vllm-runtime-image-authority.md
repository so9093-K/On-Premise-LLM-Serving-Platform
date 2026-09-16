# ADR-0028: Unified vLLM runtime image authority

## Status

Accepted

## Context

26B/12B Main Model profile, embedding, embedding-ko, risk-prompt는 2026-07-24부터
`ops/images/vllm-unified/Dockerfile`이 만드는 하나의 derived vLLM artifact를 사용한다.
Gemma4 multimodal patch와 Kanana compatibility patch도 같은 image 안에서 관리되고,
각 모델은 model id, revision, runner, command, resource/capability 설정으로 동작을 구분한다.

하지만 persistent `.env`에는 `VLLM_IMAGE`, `EMBEDDING_KO_VLLM_IMAGE`,
`RISK_VLLM_IMAGE`가 나란히 존재해 하나의 artifact가 여러 독립 authority를 가진 것처럼
표현된다. 과거 상태에서는 runtime별 값이 서로 다를 수도 있고 local build는 그런 operator
값을 의도적으로 덮어쓰지 않으므로, 기존 key를 즉시 삭제하면 운영 의미를 잃을 수 있다.

Main Model의 `AUDIO_VLLM_IMAGE`는 일부 profile이 명시적으로 선택하는 image override이며
shared runtime pin과 다른 lifecycle 의미를 가진다.

## Decision

`VLLM_IMAGE`를 일반 vLLM runtime artifact의 단일 persistent image authority로 둔다.
Main Model의 기본 runtime, embedding, embedding-ko, risk-prompt는 동일한 qualified artifact를
소비하며 모델별 차이는 image key가 아니라 각 runtime/model configuration이 소유한다.

`EMBEDDING_KO_VLLM_IMAGE`와 `RISK_VLLM_IMAGE`는 migration 기간 동안 기존 `.env`와 Compose
상태를 해석하기 위한 deprecated compatibility projection으로 유지한다. legacy 값이
`VLLM_IMAGE`와 다르면 자동으로 덮어쓰거나 삭제하지 않고 그대로 보존하며 진단에서
명시적으로 드러낸다. divergence가 존재하는 host는 제거 단계 전에 operator가 의도를
확인해야 한다.

`AUDIO_VLLM_IMAGE`는 Main Model profile-specific image override 계약이 있으므로 이 migration의
제거 대상에 포함하지 않는다.

새 shared artifact의 deployment-time promotion authority는
`VLLM_UNIFIED_IMAGE_TO_DEPLOY`다. 기존 `RISK_VLLM_IMAGE_TO_DEPLOY` compatibility input의
퇴역은 persistent env migration과 별도 변경으로 다룬다.

향후 특정 runtime에 별도 image authority가 다시 필요하다면 단순 env override를 먼저
추가하지 않는다. 독립 build artifact, qualification, promotion, rollback lifecycle이 실제로
필요하다는 근거가 있을 때 그 artifact boundary와 함께 새 authority를 정의한다.

## Consequences

| Positive | Negative |
|---|---|
| 하나의 qualified vLLM artifact와 persistent authority가 대응해 release/provenance 상태공간이 줄어든다. | migration 기간에는 legacy projection과 canonical authority가 함께 존재한다. |
| 모델별 runtime 설정과 runtime software artifact의 책임이 분리된다. | 기존 host의 divergent legacy pin은 자동 정리할 수 없고 operator audit가 필요하다. |
| 향후 vLLM candidate qualification 결과를 하나의 digest에 연결하기 쉬워진다. | Compose와 env contract의 최종 제거는 별도 단계가 필요하다. |

## Operational impact

- 신규 운영 판단에서 shared vLLM artifact를 확인할 때는 `VLLM_IMAGE`를 기준으로 한다.
- `EMBEDDING_KO_VLLM_IMAGE` 또는 `RISK_VLLM_IMAGE`가 `VLLM_IMAGE`와 다르면 compatibility
  override로 보존하되 migration 대상임을 진단한다.
- 일반 full deploy는 image promotion input이 없으면 현재 pin을 바꾸지 않는 PR #32의
  invariant를 계속 유지한다.
- shared image promotion은 immutable registry digest를 사용한다.
- `AUDIO_VLLM_IMAGE`는 Main Model profile override로 계속 독립적으로 해석한다.

## Migration notes

1. Resolver와 문서에서 `VLLM_IMAGE`를 canonical authority로 선언하고 divergent legacy pin을
   보존한 채 경고한다.
2. 실제 개발/운영 host에서 legacy key의 존재와 divergence를 audit한다.
3. `legacy == VLLM_IMAGE`이거나 operator가 명시적으로 shared authority로 수렴시키기로 한
   host만 migration한다.
4. audit가 끝난 뒤 신규 env 생성과 Compose consumer에서 legacy projection을 제거한다.
5. 마지막 제거 PR에서 `EMBEDDING_KO_VLLM_IMAGE`, `RISK_VLLM_IMAGE`를
   `configs/env_contract.yaml.removed_keys`에 등록해 명시적으로 tombstone 처리한다.
6. persistent migration이 끝난 뒤 `RISK_VLLM_IMAGE_TO_DEPLOY` compatibility input의 제거를
   별도 검토한다.

Historical ADR은 당시 계약을 설명하는 기록이므로 과거 문구를 현재 vocabulary로 다시 쓰지
않는다.

## Related

- ADR-0010: ColBERT 제거 및 dense Korean retrieval runtime
- ADR-0013: env lifecycle non-destructive sync
- ADR-0014: image validation policy
- ADR-0018: GPU VRAM admission and per-profile runtime image
- `ops/images/vllm-unified/README.md`
- `scripts/lib/vllm_unified_image.sh`
