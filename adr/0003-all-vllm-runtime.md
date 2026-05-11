# ADR-0003: All Major Models as vLLM Runtime

## Status

Accepted

## Context

Main LLM, Embedding, Prompt, Siren 모델을 vLLM 기반으로 배포한다는 요구사항이 확정되었다.

## Decision

각 모델을 독립 vLLM 인스턴스로 구성한다. Prompt/Siren raw vLLM output은 Risk Adapter가 해석한다.

## Consequences

| Positive | Negative |
|---|---|
| runtime 일관성 | vLLM 인스턴스별 CUDA overhead 증가 |
| 모델별 readiness 분리 | 48GB GPU budget이 타이트함 |
| API Adapter contract 명확 | label parser와 chat template 검증 필요 |
