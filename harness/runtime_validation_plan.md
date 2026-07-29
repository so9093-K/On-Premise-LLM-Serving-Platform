# 런타임 검증 계획

이 문서는 실제 Docker/GPU/vLLM 환경에서 무엇을 검증해야 하는지 정의한다. 정적 release gate와 live runtime gate를 혼동하지 않기 위해 별도로 유지한다.

## 검증 리포트 규칙

`python scripts/validation/runtime_validation.py`는 `reports/runtime/runtime_validation_*.json`과 `.md`를 생성한다. timestamp가 붙은 live report는 target host의 증빙이며 release package에는 포함하지 않는다.

## 필수 Gate

| Gate | 명령 | Runtime 필요 |
|---|---|---|
| 정적 계약 검증 | `make validate` | 아니오 |
| 설정 전용 runtime validation | `python scripts/validation/runtime_validation.py --config-only` | 아니오 |
| full-stack readiness | `make ready-full` | 예 |
| live runtime validation | `make runtime-validate` | 예 |
| operator evidence 생성 | `make operator-reports` | 선택, 최신 live report가 있으면 반영 |

## Kanana Risk Runtime 규칙

Risk detector는 다른 served model과 같은 unified vLLM 이미지를 사용하며, `RISK_VLLM_IMAGE`는 그 이미지의 risk-prompt 소비 경로다. `make risk-vllm-config-check`는 image label, patch metadata, Kanana Prompt config load를 확인해야 한다. production 승격 시 patch verify를 skip하면 안 된다.

## 계약 검증용 marker

아래 원문은 기존 governance validation과 호환하기 위해 보존한다. 설명 기준은 위 한국어 본문이다.

- Runtime Validation Plan
- Validation Report Rule
- reports/runtime/runtime_validation_
- does not record raw prompts
- Required Gates
- Kanana Risk Runtime Rule
