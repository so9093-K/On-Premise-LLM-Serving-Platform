# 06. 아키텍처

## 1. 목표

Gateway 하나를 기준으로 LLM, Embedding, Risk Signal 기능을 제공하고, 내부 vLLM runtime과 detector 구성을 애플리케이션에서 숨긴다.

## 2. 계층 구조

```text
Client / Application
  -> Gateway API :9400
      -> Main LLM vLLM :9401
      -> Embedding vLLM :9402
      -> Risk Adapter :9405
            -> Prompt Risk vLLM :9403
```

## 3. Public API와 Runtime API

| 구분 | 노출 대상 | 설명 |
|---|---|---|
| Gateway `/v1/*` | 애플리케이션 | bearer API key 필요 |
| Gateway `/health` | health check | 단순 liveness |
| Gateway `/ready`, `/metrics` | 운영자/Prometheus | admin auth 또는 내부망 보호 |
| Risk Adapter `/v1/*` | 내부 서비스 | internal service token 필요 |
| vLLM `/v1/*` | 내부 runtime | Gateway/Risk Adapter에서만 호출 |

## 4. Risk Signal Flow

Risk Adapter는 enabled detector output을 `<SAFE>` 또는 `<UNSAFE-Ax/Ix>` 형태의 signal로 정규화한다. 기본 구성은 prompt detector만 사용하며, retired detector route는 호환성 정책에 따라 410 Gone을 반환한다. `allow`, `block`, `decision`, `action` 같은 policy field는 반환하지 않는다.

## 5. Gateway Chat Flow

Gateway는 요청을 먼저 검증하고, 허용된 parameter와 model id만 vLLM upstream으로 전달한다. upstream schema 오류, timeout, model unavailable 상태는 공통 error response로 표현한다.
