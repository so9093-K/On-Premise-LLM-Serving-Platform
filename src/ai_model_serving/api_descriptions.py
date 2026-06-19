from __future__ import annotations

# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------

GATEWAY_TAGS_METADATA = [
    {
        "name": "Operations",
        "description": "`/health`는 process liveness, `/ready`는 vLLM과 Risk Adapter 전체 dependency 상태를 확인합니다.",
    },
    {
        "name": "Monitoring",
        "description": "Prometheus scrape용 metric 엔드포인트입니다. 운영 환경에서는 admin token 또는 내부망으로 보호합니다.",
    },
    {
        "name": "Models",
        "description": "Gateway가 외부 호출자에게 노출하는 logical model catalog입니다. `/v1/models` 응답은 모델별 `capabilities`와 사용자 조정 가능 `request_parameters`를 함께 제공합니다.",
    },
    {
        "name": "Chat",
        "description": "OpenAI 호환 chat completions API입니다. `stream`, `tools`, structured outputs, `reasoning`, `logprobs` 등 고급 기능을 지원합니다.",
    },
    {
        "name": "Embeddings",
        "description": "`local-embed`와 `local-embed-ko`를 통한 embedding API입니다. 요청 파라미터는 Gateway contract로 검증합니다.",
    },
    {
        "name": "Retrieval",
        "description": "`local-embed-ko` 기본, `local-embed` 선택 dense cosine 문서 관련도 평가 API입니다.",
    },
    {
        "name": "Risk",
        "description": (
            "signal-only risk assessment API입니다. `allow`, `block`, `decision`, `action` 같은 정책 판단 필드는 반환하지 않습니다. "
            "최종 허용·차단 결정은 Gateway 밖 product policy layer가 담당합니다.\n\n"
            "**Sensitive Data Protection** — PII Protection + Secret Exposure Signal:\n"
            "- **PII Protection** (D1-D3, D5): 주민등록번호, 이메일, 전화번호, 신용카드, IP 주소 탐지\n"
            "- **Secret Exposure** (D4, D5): API 키, JWT, private key, 비밀번호, DB URL 탐지\n\n"
            "**Prompt detector** (`risk-prompt`) — Prompt Injection / Prompt Leaking 탐지:\n"
            "- system/developer instruction 무시 유도\n"
            "- 숨겨진 system prompt 출력 요구\n"
            "- 역할극(DAN, unrestricted AI 등) jailbreak\n"
            "- 문서·웹페이지 안에 숨겨진 간접 prompt injection\n"
            "- 연결된 도구로 시크릿·파일·메일 탈취 유도"
        ),
    },
]

GATEWAY_DESCRIPTION_TEMPLATE = """
## 빠른 시작

1. `GET /health` — Gateway process liveness
2. `GET /ready` — vLLM, Risk Adapter 전체 dependency readiness
3. `GET /v1/models` — logical model id, capability, 사용자 조정 가능 parameter 목록
4. `POST /v1/chat/completions` — chat completion (`local-main`)
5. `POST /v1/embeddings` — embedding 생성 (`local-embed`, `local-embed-ko`)
6. `POST /v1/retrieval/*` — 문서 관련도 재순위·점수 계산 (`local-embed-ko` 기본)
7. `POST /v1/risk/*` — prompt risk signal

## 인증

- **bearerAuth** — `/v1/*` 사용자 API: `Authorization: Bearer <API_KEY>`
- **adminBearerAuth** — `/ready`, `/metrics`: `Authorization: Bearer <ADMIN_API_KEY>`

## 모델별 파라미터 확인

모델별 사용자 조정 가능 parameter는 `/v1/models[].request_parameters`가 source of truth입니다.
이 API docs는 contract reference이며, 모델별 form UI는 `/v1/models` 기반으로 구성해야 합니다.

| 모델 | 기능 |
|---|---|
| `local-main` | chat, vision, tools, structured outputs, logprobs |
| `local-embed` | embeddings (`dimensions`, `truncate_prompt_tokens`) |
| `local-embed-ko` | Korean dense retrieval default, embeddings fixed at 1024 dimensions |
| `risk-prompt` | prompt risk signal — 사용자 sampling parameter 없음 |

## Readiness

- `/health` — process liveness (인증 없음)
- `/ready` — 전체 dependency readiness (admin auth 필요)
- **HTTP 200** `status: ready` — serving 가능
- **HTTP 503** `status: not_ready` — 로딩 중 또는 dependency 불가 (`not_ready_dependencies` 필드 참고)
"""

# ---------------------------------------------------------------------------
# Risk Adapter
# ---------------------------------------------------------------------------

RISK_ADAPTER_TAGS_METADATA = [
    {
        "name": "Operations",
        "description": "`/health`는 adapter process liveness, `/ready`는 detector vLLM dependency 상태를 확인합니다.",
    },
    {
        "name": "Monitoring",
        "description": "Risk Adapter와 detector별 signal metric입니다. 운영 환경에서는 admin token 또는 내부망으로 보호합니다.",
    },
    {
        "name": "Risk Signal",
        "description": (
            "내부 detector 호출 결과를 signal-only response로 정규화합니다. 최종 정책 결정 필드는 반환하지 않습니다.\n\n"
            "**Sensitive Data Protection**:\n"
            "- **PII Protection** (D1-D3, D5) — Presidio + Korean recognizer 기반 개인정보 탐지\n"
            "- **Secret Exposure** (D4, D5) — regex/entropy 기반 시크릿·자격증명 탐지\n\n"
            "**Prompt detector** — Prompt Injection / Leaking 탐지:\n"
            "지시 무시, system prompt 탈취, roleplay jailbreak, 간접 injection, tool abuse"
        ),
    },
]

RISK_ADAPTER_DESCRIPTION_TEMPLATE = """
## 개요

내부 Risk Adapter API입니다. Gateway 또는 내부 호출자가 사용합니다.

## Detector 역할

| Detector | 유형 | 담당 신호 | Risk 코드 |
|---|---|---|---|
| **PII Protection** | local (presidio + regex) | 개인정보 노출 | D1, D2, D3, D5 |
| **Secret Exposure** | local (regex + entropy) | 시크릿·자격증명 노출 | D4, D5 |
| **Prompt** | vLLM (`risk-prompt`) | Prompt Injection / Prompt Leaking | A1, A2 |

- PII Protection과 Secret Exposure는 in-process 로컬 탐지로 외부 모델 호출이 없습니다.
- Prompt detector 출력 `<SAFE>`, `<UNSAFE-A1>` 같은 label을 표준 signal-only response로 정규화합니다.
- 정책 판단 필드(`allow`, `block`, `decision`, `action`)는 반환하지 않습니다.
- 원문 PII/Secret 값은 응답, 로그, metric에 포함되지 않습니다.

## Aggregate 실행 순서

`pii → secret → prompt` 순서로 sequential 실행. 어느 detector든 탐지하면 `risk_detected: true`.

## Readiness

- enabled vLLM detector runtime 준비 → HTTP 200 + `phase: serving`
- 모델 로딩 중 → HTTP 503 + `phase: waiting_for_dependencies`
"""
