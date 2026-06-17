# ADR-0016: xgrammar disable-any-whitespace Structured Output Backend

## Status

Accepted

## Context

`local-main`(Gemma 4 26B-A4B FP8) vLLM runtime의 structured output backend는 `--structured-outputs-config '{"backend":"auto"}'`로 구동되며, `auto`는 xgrammar를 우선 선택한다.

운영 중 `response_format: {type: json_schema}` 요청에서 다음 증상이 반복 관찰됐다.

- 실제 JSON 내용 생성 이후 `\n  `(들여쓰기 포함 줄바꿈)만 `max_tokens`(8192)까지 반복 생성됨
- `trailing_whitespace_ratio ≈ 0.977` — 전체 completion의 97.7%가 whitespace
- Gateway non-stream: 502 `UPSTREAM_SCHEMA_ERROR` (응답 body JSON 파싱 실패)
- Gateway stream / Direct vLLM stream: 200이지만 invalid JSON (루트 object 닫는 `}` 누락)
- Gateway와 Direct vLLM 양쪽에서 동일 byte count, 동일 whitespace 비율로 재현됨 → Gateway 라우팅 문제 아님

근본 원인은 xgrammar의 `any_whitespace` 기능이다. 중첩 배열(`evidence: []` 등)의 닫는 `]` 이후, 루트 object 닫는 `}` 로 전이하지 못하고 whitespace 토큰만 허용하는 stuck state에 진입한다. vLLM 공식 이슈에서 확인된 버그다.

- [vLLM PR #12744](https://github.com/vllm-project/vllm/pull/12744) (2025-02-26): V0 engine fix — env var `VLLM_XGRAMMAR_DISABLE_ANY_WHITESPACE`
- [vLLM PR #15316](https://github.com/vllm-project/vllm/pull/15316) (2025-03-22): V1 engine fix — `xgrammar:disable-any-whitespace` backend 문자열 지원 (해당 PR 기준 API)

`gemma4-0505-cu129`(2025-05-05 빌드)는 PR #15316을 포함할 것으로 예상했으나, 이 태그는 vLLM main 브랜치가 아니라 Gemma 4 지원을 위한 custom feature-branch 빌드였다. 실제 배포 시 다음 오류로 vLLM 컨테이너가 exit code 2로 종료됐다.

```
vllm serve: error: argument --structured-outputs-config: 1 validation error for StructuredOutputsConfig
backend
  Input should be 'auto', 'xgrammar', 'guidance', 'outlines' or 'lm-format-enforcer'
  input_value='xgrammar:disable-any-whitespace'
```

이미지를 `gemma4-unified-cu129`(2026-06-03 빌드, vLLM main 기반)로 교체하면서, 해당 이미지의 `StructuredOutputsConfig` API가 변경된 것을 확인했다. `xgrammar:disable-any-whitespace` 단일 backend 문자열 방식 대신, `backend: "xgrammar"` + `disable_any_whitespace: bool` 분리 필드 방식으로 재설계됐다.

대안으로 `outlines` 백엔드도 검토했으나 채택하지 않았다.

- vLLM V1 engine에서 v0.8 초기에 broken, 이후 `outlines_core`(subset)로 부분 복구됨
- CFG grammar 지원이 V1에서 제거됨
- vLLM 장기 방향은 xgrammar-first이며, V0 engine 제거와 함께 outlines 의존성도 제거 예정([vLLM Issue #18571](https://github.com/vllm-project/vllm/issues/18571))
- `outlines`는 FSM 방식으로 xgrammar 대비 성능이 낮음(최대 5x TPOT 차이)

## Decision

vLLM main LLM structured output backend를 `auto`(xgrammar)에서 `xgrammar` + `disable_any_whitespace: true`로 변경한다.

동시에 vLLM 이미지를 `gemma4-0505-cu129`(custom feature-branch)에서 `gemma4-unified-cu129`(vLLM main 기반, `Gemma4ForCausalLM` 지원 확인)으로 교체한다.

변경 위치:

| 파일 | 변경 |
|---|---|
| `configs/model_serving.yaml` | `structured_outputs.backend: "xgrammar"`, `disable_any_whitespace: true` 추가 |
| `ops/compose/full-stack.private-network.yaml` | `--structured-outputs-config '{"backend":"xgrammar","disable_any_whitespace":true,"enable_in_reasoning":true}'` |
| `src/.../vllm_commands.py` | `disable_any_whitespace` 필드를 config JSON에 포함하도록 수정 |
| `configs/recommended_images.yaml` | 기본 이미지 `gemma4-unified-cu129`로 업데이트 |

두 config 파일은 source of truth가 분리되어 있으므로(model_serving.yaml은 application config, compose는 runtime config), 불일치를 CI에서 조기 감지하는 contract test를 추가한다: `test_compose_structured_outputs_config_matches_model_serving_yaml`.

## Consequences

| Positive | Negative |
|---|---|
| xgrammar `any_whitespace` stuck state 버그가 제거됨 | vLLM `StructuredOutputsConfig` API는 버전마다 변경될 수 있으므로 이미지 교체 시 재확인 필요 |
| vLLM 장기 방향(xgrammar-first)과 일치하는 backend를 유지함 | `any_whitespace` 비활성화로 모델 출력 JSON이 pretty-print 대신 compact 포맷이 될 수 있음(파싱 코드에는 무관) |
| compose ↔ model_serving.yaml 불일치를 CI가 즉시 탐지함 | custom feature-branch 이미지(`gemma4-0505`)와 main 기반 이미지(`gemma4-unified`) 간 동작 차이가 존재할 수 있음 |
| vLLM main 기반 이미지로 전환해 향후 PR 픽스 수용이 용이해짐 | |

## Related

- [vLLM PR #12744](https://github.com/vllm-project/vllm/pull/12744) — V0 engine any_whitespace fix
- [vLLM PR #15316](https://github.com/vllm-project/vllm/pull/15316) — V1 engine disable-any-whitespace support
- [vLLM Issue #18571](https://github.com/vllm-project/vllm/issues/18571) — V0 engine deprecation, outlines 제거 예정
- [ADR-0015](0015-main-llm-20k-o3-runtime-target.md) — Main LLM runtime target
- `configs/model_serving.yaml`
- `ops/compose/full-stack.private-network.yaml`
- `configs/recommended_images.yaml`
- `src/ai_model_serving/runtime_validation/vllm_commands.py`
- `tests/contract/governance/test_runtime_docs_and_harness.py`
