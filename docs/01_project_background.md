# 01. 프로젝트 배경

## 1. 역할

이 문서는 과거 원천 프로젝트에서 가져온 의도와 현재 플랫폼에서 유지하는 원칙만 짧게 기록한다. 현재 canonical source는 `README.md`, `docs/00_executive_summary.md`, `docs/02_decision_register.md`, `specs/`, `configs/`, `src/`이다.

## 2. 참고한 원천

| 원천 | 현재 플랫폼에 남긴 것 | 현재 위치 |
|---|---|---|
| Prompt Risk Signal API | signal-only 원칙, prompt-only request, fail-safe system signal, privacy logging | `docs/specs/risk_signal_contract.md`, `configs/model_serving.yaml` |
| llm-deploy | vLLM OpenAI-compatible endpoint, embedding runtime, monitoring/harness 운영 의도 | `configs/model_serving.yaml`, `ops/`, `harness/` |

## 3. 제외한 항목

과거 프로젝트 코드, 상세 inventory, transition report, fake runtime path는 현재 플랫폼 목적에 필요하지 않으므로 release package에 포함하지 않는다.

## 4. ADR-0001 처리

ADR-0001은 별도 파일로 유지하지 않는다. 현재 결정은 `docs/02_decision_register.md`의 D-001로 관리한다.
