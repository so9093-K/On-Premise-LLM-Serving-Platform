# 00. 요약

## 1. 목적

이 플랫폼은 vLLM 기반 LLM, Embedding, Risk Signal 서비스를 Gateway 중심으로 묶어 애플리케이션이 하나의 표준 API로 모델 기능을 사용할 수 있게 하는 운영 패키지다.

## 2. 현재 상태

| 영역 | 상태 |
|---|---|
| Gateway | FastAPI 구현 포함. `/v1/chat/completions`, `/v1/embeddings`, `/v1/risk/*` 제공 |
| Risk Adapter | detector 출력을 signal-only response로 정규화 |
| 계약 | OpenAPI, JSON Schema, contract test 포함 |
| 실행 | local app-only와 full-stack compose 경로 제공 |
| 문서 | 한국어 설명을 기본으로 하고 API/env/명령 식별자만 영어 원문 유지 |
| 모니터링 | Prometheus/Grafana/DCGM exporter template 기본 활성화 |

## 3. 핵심 결정

| 결정 | 내용 |
|---|---|
| Gateway 단일 진입점 | 외부 애플리케이션은 `9400` Gateway를 기준으로 연동 |
| signal-only risk | Risk Adapter는 정책 결정을 하지 않고 위험 신호만 반환 |
| 문서 기본 활성화 | `/docs`, `/redoc`, `/openapi.json`은 기본 활성화 |
| 모니터링 기본 활성화 | compose/staging/production-like 검증에서는 모니터링을 먼저 켜 둠 |
| 레거시 제거 | 과거 원천 프로젝트 상세 보고서와 fake runtime path는 release에서 제거 |

## 4. 목표 아키텍처

```text
Client
  -> Gateway :9400
      -> Main LLM vLLM :9401
      -> Embedding vLLM :9402
      -> Risk Adapter :9405
            -> Prompt detector vLLM :9403

Prometheus :9410
Grafana    :9411
DCGM       :9412
```

## 5. 운영 전 필수 검증

실제 GPU host에서 vLLM 동시 기동, VRAM peak, latency, timeout, Prometheus scrape, Grafana real-data rendering을 측정해야 한다.
