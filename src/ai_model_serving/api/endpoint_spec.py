from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class EndpointSpec:
    service: str        # "gateway" | "risk-adapter"
    method: str         # "GET" | "POST"
    path: str
    operation_id: str
    tag: str            # OpenAPI tag name
    summary: str
    description: str
    auth: str           # "none" | "admin" | "public_api" | "internal_service"
    exposure: str       # "public_gateway" | "internal_only" | "operations_network"
                        # | "internal_service" | "internal_or_lb_probe" | "not_served"
    lifecycle: str      # "stable" | "retired" | "removed"
    status_code: int    # canonical success status (200/202 for stable, 410 for retired)
    replacement: str | None          # replacement path for retired endpoints
    request_schema: str | None       # e.g. "chat_completion_request.schema.json"
    response_schema: str | None      # e.g. "chat_completion_response.schema.json"


_SKIP_SCHEMA_LIFECYCLES: frozenset[str] = frozenset({"removed", "retired"})

RouteKey = tuple[str, str]  # (method, path)


def schema_maps_from_specs(
    endpoints: Sequence[EndpointSpec],
) -> tuple[dict[RouteKey, str], dict[RouteKey, str]]:
    """Derive request_schemas and response_schemas dicts from an EndpointSpec list.

    Retired endpoints always return 410 (handled by the error-response injector),
    and removed endpoints have no route, so both are excluded from the maps.
    """
    request_schemas: dict[RouteKey, str] = {
        (s.method, s.path): s.request_schema
        for s in endpoints
        if s.request_schema is not None and s.lifecycle not in _SKIP_SCHEMA_LIFECYCLES
    }
    response_schemas: dict[RouteKey, str] = {
        (s.method, s.path): s.response_schema
        for s in endpoints
        if s.response_schema is not None and s.lifecycle not in _SKIP_SCHEMA_LIFECYCLES
    }
    return request_schemas, response_schemas


# ---------------------------------------------------------------------------
# Gateway endpoints (port 9400)
# ---------------------------------------------------------------------------

GATEWAY_ENDPOINTS: list[EndpointSpec] = [
    EndpointSpec(
        service="gateway",
        method="GET",
        path="/health",
        operation_id="getGatewayHealth",
        tag="Operations",
        summary="Liveness 확인",
        description="Process liveness probe. 항상 HTTP 200을 반환합니다. 인증 없이 접근 가능합니다.",
        auth="none",
        exposure="internal_or_lb_probe",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        service="gateway",
        method="GET",
        path="/ready",
        operation_id="getGatewayReadiness",
        tag="Operations",
        summary="Dependency readiness 확인",
        description=(
            "내부 readiness endpoint입니다. Gateway가 의존하는 vLLM runtime과 Risk Adapter의 준비 상태를 확인합니다. "
            "모델 로딩 중에는 HTTP 503을 반환하며, body의 `not_ready_dependencies`에 "
            "아직 준비되지 않은 dependency 목록과 `message`가 포함됩니다."
        ),
        auth="admin",
        exposure="internal_only",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema="readiness_response.schema.json",
    ),
    EndpointSpec(
        service="gateway",
        method="GET",
        path="/metrics",
        operation_id="getGatewayMetrics",
        tag="Monitoring",
        summary="Prometheus metrics 조회",
        description="Prometheus가 scrape하는 Gateway metric 엔드포인트입니다. 운영 환경에서는 admin token 또는 내부망으로 보호합니다.",
        auth="admin",
        exposure="operations_network",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        service="gateway",
        method="GET",
        path="/internal/main-model/drain-status",
        operation_id="getMainModelDrainStatus",
        tag="Runtime Control",
        summary="메인 모델 요청 drain 상태 조회",
        description=(
            "Admin Sidecar가 모델 교체 전에 진행 중인 local-main 요청 수를 확인하는 "
            "내부 서비스 전용 endpoint입니다. OpenAPI UI에는 노출하지 않습니다."
        ),
        auth="internal_service",
        exposure="internal_service",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        service="gateway",
        method="GET",
        path="/v1/models",
        operation_id="listModels",
        tag="Models",
        summary="사용 가능한 모델 목록",
        description=(
            "Gateway가 외부 호출자에게 노출하는 logical model id, capability, 사용자 조정 가능 request parameter 목록입니다. "
            "catalog 성격의 엔드포인트이므로 vLLM 로딩 상태와 무관하게 항상 목록을 반환합니다. "
            "현재 serving 가능 여부는 `/ready`의 `status`와 `not_ready_dependencies`를 확인하세요. "
            "클라이언트 UI는 각 item의 `request_parameters`를 읽어 모델별 입력 form을 구성할 수 있습니다."
        ),
        auth="public_api",
        exposure="public_gateway",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema="model_list_response.schema.json",
    ),
    EndpointSpec(
        service="gateway",
        method="POST",
        path="/v1/chat/completions",
        operation_id="createChatCompletion",
        tag="Chat",
        summary="Chat completion 생성",
        description=(
            "`local-main`을 통한 chat completion API입니다. OpenAI 호환 bounded subset을 제공합니다.\n\n"
            "Gateway가 model id, 입력 modality, token limit, tool-call 지원, parameter allowlist를 검증합니다.\n\n"
            "- `stream=true` — vLLM SSE chunk를 버퍼링 없이 `text/event-stream`으로 relay\n"
            "- `response_format`\n"
            "  - `json_object` — JSON mode. messages에 명시적 JSON 지시문 필요, schema 일치 미보장\n"
            "  - `json_schema` — Structured Outputs. root `object` 필수, `additionalProperties: false` 필수, "
            "optional field는 nullable union(`[\"type\", \"null\"]`) 으로 표현, external `$ref` 불가\n"
            "- `logprobs`, `top_logprobs`, `logit_bias` — Gateway policy 안에서 upstream에 전달"
        ),
        auth="public_api",
        exposure="public_gateway",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema="chat_completion_request.schema.json",
        response_schema="chat_completion_response.schema.json",
    ),
    EndpointSpec(
        service="gateway",
        method="POST",
        path="/v1/embeddings",
        operation_id="createEmbedding",
        tag="Embeddings",
        summary="Embedding vector 생성",
        description=(
            "`local-embed` 및 `local-embed-ko`를 통해 텍스트의 embedding vector를 생성합니다. "
            "요청 파라미터는 Gateway contract로 검증하며, 지원하지 않는 파라미터는 차단합니다."
        ),
        auth="public_api",
        exposure="public_gateway",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema="embedding_request.schema.json",
        response_schema="embedding_response.schema.json",
    ),
    EndpointSpec(
        service="gateway",
        method="POST",
        path="/v1/risk/detectors/prompt/assessments",
        operation_id="assessPromptRisk",
        tag="Risk",
        summary="Prompt 위협 탐지 신호",
        description=(
            "Prompt attack detector의 위험 신호만 반환합니다. "
            "정책 판단 필드(`allow`, `block`, `decision` 등)는 포함되지 않으며, "
            "최종 허용·차단 결정은 Gateway 밖 product policy layer가 담당합니다."
        ),
        auth="public_api",
        exposure="public_gateway",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        service="gateway",
        method="POST",
        path="/v1/risk/detectors/pii/assessments",
        operation_id="assessPIIRisk",
        tag="Risk",
        summary="PII Protection 탐지 신호",
        description=(
            "**PII Protection detector** — Presidio Analyzer(optional) + Korean custom recognizer 기반 로컬 탐지.\n\n"
            "탐지 코드:\n"
            "- **D1** Personal Identifier: KR_RRN, KR_FRN, KR_PASSPORT, KR_DRIVER_LICENSE, PERSON\n"
            "- **D2** Contact and Location: EMAIL_ADDRESS, PHONE_NUMBER, ADDRESS\n"
            "- **D3** Financial Identifier: CREDIT_CARD, KR_BRN, BANK_ACCOUNT_CANDIDATE\n"
            "- **D5** Network/Infrastructure: IP_ADDRESS, URL\n\n"
            "원문 PII 값은 응답에 포함되지 않습니다. `span_count`로 entity별 탐지 개수를 제공합니다.\n"
            "탐지 결과는 최종 정책 판단이 아닌 진단 signal로 취급합니다."
        ),
        auth="public_api",
        exposure="public_gateway",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        service="gateway",
        method="POST",
        path="/v1/risk/detectors/secret/assessments",
        operation_id="assessSecretRisk",
        tag="Risk",
        summary="Secret Exposure 탐지 신호",
        description=(
            "**Secret Exposure detector** — curated regex + entropy 기반 로컬 탐지. 외부 CLI 없이 Python 내부 scanner로 동작합니다.\n\n"
            "탐지 코드:\n"
            "- **D4** Secret/Credential: OPENAI_API_KEY, AWS_ACCESS_KEY_ID, GITHUB_TOKEN, GITLAB_TOKEN, HUGGINGFACE_TOKEN, JWT, PRIVATE_KEY_BLOCK, PASSWORD_ASSIGNMENT, GENERIC_SECRET_CANDIDATE\n"
            "- **D5** Network/Infrastructure: DATABASE_URL\n\n"
            "응답, 로그, metric label에 원문 시크릿 값을 남기지 않습니다. `span_count`로 탐지 개수를 제공합니다.\n"
            "탐지 결과는 최종 정책 판단이 아닌 진단 signal로 취급합니다."
        ),
        auth="public_api",
        exposure="public_gateway",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        service="gateway",
        method="POST",
        path="/v1/risk/detectors/siren/assessments",
        operation_id="assessRetiredSirenRisk",
        tag="Risk",
        summary="Retired Siren detector 신호",
        description=(
            "`risk-siren` detector는 retired 상태입니다. "
            "이 endpoint는 항상 410 Gone을 반환합니다. "
            "`/v1/risk/detectors/prompt/assessments` 또는 `/v1/risk/assessments`를 사용하세요."
        ),
        auth="public_api",
        exposure="public_gateway",
        lifecycle="retired",
        status_code=410,
        replacement="/v1/risk/detectors/prompt/assessments",
        request_schema=None,
        response_schema="common_error.schema.json",
    ),
    EndpointSpec(
        service="gateway",
        method="POST",
        path="/v1/risk/assessments",
        operation_id="assessRisk",
        tag="Risk",
        summary="통합 Risk 신호",
        description=(
            "configured enabled detectors 결과를 aggregate한 통합 risk assessment입니다. "
            "PII Protection(D1-D3, D5), Secret Exposure(D4, D5), Prompt Injection(A1, A2) 신호를 통합합니다. "
            "enabled detector 중 하나라도 위험 신호를 탐지하면 `risk_detected: true`를 반환합니다."
        ),
        auth="public_api",
        exposure="public_gateway",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        service="gateway",
        method="POST",
        path="/v1/retrieval/rerank",
        operation_id="rerankDocuments",
        tag="Retrieval",
        summary="문서 관련도 재순위 정렬",
        description=(
            "query와 documents 목록을 받아 관련도 점수로 내림차순 정렬한 결과를 반환합니다.\n\n"
            "- `score_mode=dense_cosine` — `local-embed-ko` 기본, `local-embed`도 명시 사용 가능\n\n"
            "모델이 요청한 `score_mode`를 지원하지 않으면 422를 반환합니다. `top_n`은 상위 n개만 반환합니다(1–32)."
        ),
        auth="public_api",
        exposure="public_gateway",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema="retrieval_rerank_request.schema.json",
        response_schema="retrieval_rerank_response.schema.json",
    ),
    EndpointSpec(
        service="gateway",
        method="POST",
        path="/v1/retrieval/score",
        operation_id="scoreDocuments",
        tag="Retrieval",
        summary="문서 관련도 점수 계산 (입력 순서 유지)",
        description=(
            "query와 documents 목록을 받아 관련도 점수를 계산합니다. 입력 순서를 유지합니다.\n\n"
            "재순위 정렬이 필요하면 `/v1/retrieval/rerank`를 사용하세요.\n\n"
            "`top_n`은 이 endpoint에서 지원하지 않습니다 (422). 지원 score mode는 `dense_cosine` 하나입니다."
        ),
        auth="public_api",
        exposure="public_gateway",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema="retrieval_score_request.schema.json",
        response_schema="retrieval_score_response.schema.json",
    ),
    # ------------------------------------------------------------------ admin runtime control
    EndpointSpec(
        service="gateway",
        method="GET",
        path="/admin/runtimes",
        operation_id="listRuntimes",
        tag="Runtime Control",
        summary="런타임 상태 조회",
        description=(
            "제어 가능한 vLLM 런타임(embedding, embedding_ko, risk_prompt)의 현재 상태를 반환합니다.\n\n"
            "`state`: 런타임 상태 — `active`(서비스 중) / `stopped`(컨테이너 중지, VRAM 회수) / `starting`(전환 중, 일시적).\n\n"
            "`container_status`: 실제 Docker 컨테이너 상태(참고용). "
            "상태 전환은 `PATCH /admin/runtimes/{service_key}`로 수행합니다."
        ),
        auth="admin",
        exposure="operations_network",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        service="gateway",
        method="PATCH",
        path="/admin/runtimes/{service_key}",
        operation_id="transitionRuntime",
        tag="Runtime Control",
        summary="런타임 상태 전환",
        description=(
            "`desired_state`에 지정한 목표 상태로 런타임을 전환합니다.\n\n"
            "- **`active`** — 컨테이너를 시작하고 gateway 라우팅을 복구합니다. "
            "이미 `active`면 no-op.\n"
            "- **`stopped`** — 컨테이너를 중지하고 GPU VRAM을 회수합니다. "
            "이미 `stopped`면 no-op.\n\n"
            "전환 중(`starting`) 상태에서 `stopped`를 요청하면 409를 반환합니다. "
            "Scalar UI 드롭다운에서 선택 후 Execute만 누르면 됩니다."
        ),
        auth="admin",
        exposure="operations_network",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        service="gateway",
        method="GET",
        path="/admin/main-model",
        operation_id="getMainModel",
        tag="Runtime Control",
        summary="활성 메인 모델 조회",
        description="현재 local-main 내부 프로필, gate, 마지막 전환 상태를 조회합니다.",
        auth="admin",
        exposure="operations_network",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        service="gateway",
        method="GET",
        path="/admin/main-model/profiles",
        operation_id="listMainModelProfiles",
        tag="Runtime Control",
        summary="메인 모델 프로필 조회",
        description="허용된 26B/12B 프로필과 capability·compatibility 근거를 조회합니다.",
        auth="admin",
        exposure="operations_network",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        service="gateway",
        method="POST",
        path="/admin/main-model/switch",
        operation_id="switchMainModel",
        tag="Runtime Control",
        summary="메인 모델 전환",
        description="허용된 profile ID로 비동기 전환을 시작하고 operation ID를 반환합니다.",
        auth="admin",
        exposure="operations_network",
        lifecycle="stable",
        status_code=202,
        replacement=None,
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        service="gateway",
        method="GET",
        path="/admin/main-model/operations/{operation_id}",
        operation_id="getMainModelOperation",
        tag="Runtime Control",
        summary="메인 모델 전환 작업 조회",
        description="전환 단계 및 실패 원인을 조회합니다.",
        auth="admin",
        exposure="operations_network",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema=None,
    ),
]

# ---------------------------------------------------------------------------
# Risk Adapter endpoints (port 9405)
# ---------------------------------------------------------------------------

RISK_ADAPTER_ENDPOINTS: list[EndpointSpec] = [
    EndpointSpec(
        service="risk-adapter",
        method="GET",
        path="/health",
        operation_id="getRiskAdapterHealth",
        tag="Operations",
        summary="Liveness 확인",
        description="Process liveness probe. 항상 HTTP 200을 반환합니다. 인증 없이 접근 가능합니다.",
        auth="none",
        exposure="internal_or_lb_probe",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        service="risk-adapter",
        method="GET",
        path="/ready",
        operation_id="getRiskAdapterReadiness",
        tag="Operations",
        summary="Risk Adapter readiness 확인",
        description=(
            "내부 readiness endpoint입니다. enabled detector vLLM runtime 상태를 확인합니다. "
            "모델 로딩 중에는 HTTP 503을 반환하고 `not_ready_dependencies`와 dependency별 `message`를 제공합니다."
        ),
        auth="admin",
        exposure="internal_only",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema="readiness_response.schema.json",
    ),
    EndpointSpec(
        service="risk-adapter",
        method="GET",
        path="/metrics",
        operation_id="getRiskAdapterMetrics",
        tag="Monitoring",
        summary="Prometheus metrics 조회",
        description="Prometheus가 scrape하는 Risk Adapter metric입니다. detector별 timeout, parse failure, signal count를 확인합니다.",
        auth="admin",
        exposure="operations_network",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        service="risk-adapter",
        method="POST",
        path="/v1/risk/detectors/prompt/assessments",
        operation_id="assessRiskPromptDetector",
        tag="Risk Signal",
        summary="Prompt detector 신호 — Prompt Injection / Leaking",
        description=(
            "**Prompt detector**(`risk-prompt`)만 단독 호출합니다.\n\n"
            "탐지 대상:\n"
            "- system/developer instruction 무시 유도\n"
            "- 숨겨진 system prompt·tool config 출력 요구\n"
            "- 역할극(DAN 등) jailbreak\n"
            "- 문서·웹페이지 안에 숨겨진 간접 prompt injection\n"
            "- 연결된 도구로 시크릿·파일·메일 탈취 유도\n\n"
            "일반 사이버 공격 절차·폭력·혐오 콘텐츠는 이 detector 범위 밖입니다."
        ),
        auth="internal_service",
        exposure="internal_service",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        service="risk-adapter",
        method="POST",
        path="/v1/risk/detectors/pii/assessments",
        operation_id="assessRiskPIIDetector",
        tag="Risk Signal",
        summary="PII Protection detector 신호 — 개인정보 노출 탐지",
        description=(
            "**PII Protection detector** — Presidio Analyzer(optional) + Korean custom recognizer 기반 로컬 탐지.\n\n"
            "탐지 코드:\n"
            "- **D1** Personal Identifier: KR_RRN, KR_FRN, KR_PASSPORT, KR_DRIVER_LICENSE, PERSON\n"
            "- **D2** Contact and Location: EMAIL_ADDRESS, PHONE_NUMBER, ADDRESS\n"
            "- **D3** Financial Identifier: CREDIT_CARD, KR_BRN, BANK_ACCOUNT_CANDIDATE\n"
            "- **D5** Network/Infrastructure: IP_ADDRESS, URL\n\n"
            "응답에 원문 PII 값을 포함하지 않습니다. `span_count`로 entity별 탐지 개수를 제공합니다.\n"
            "탐지 결과는 최종 정책 판단이 아닌 진단 signal로 취급합니다."
        ),
        auth="internal_service",
        exposure="internal_service",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        service="risk-adapter",
        method="POST",
        path="/v1/risk/detectors/secret/assessments",
        operation_id="assessRiskSecretDetector",
        tag="Risk Signal",
        summary="Secret Exposure detector 신호 — 시크릿·자격증명 노출 탐지",
        description=(
            "**Secret Exposure detector** — curated regex + entropy 기반 로컬 탐지. 외부 CLI 없이 Python 내부 scanner로 동작합니다.\n\n"
            "탐지 코드:\n"
            "- **D4** Secret/Credential: OPENAI_API_KEY, AWS_ACCESS_KEY_ID, GITHUB_TOKEN, GITLAB_TOKEN, HUGGINGFACE_TOKEN, JWT, PRIVATE_KEY_BLOCK, PASSWORD_ASSIGNMENT, GENERIC_SECRET_CANDIDATE\n"
            "- **D5** Network/Infrastructure: DATABASE_URL\n\n"
            "응답, 로그, metric label에 원문 시크릿 값을 남기지 않습니다. `span_count`로 탐지 개수를 제공합니다.\n"
            "탐지 결과는 최종 정책 판단이 아닌 진단 signal로 취급합니다."
        ),
        auth="internal_service",
        exposure="internal_service",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        service="risk-adapter",
        method="POST",
        path="/v1/risk/detectors/siren/assessments",
        operation_id="assessSirenDetector",
        tag="Risk Signal",
        summary="Siren detector 신호 (제거됨)",
        description="이 route는 제거되었습니다. Risk Adapter에서 siren detector는 serving되지 않습니다.",
        auth="none",
        exposure="not_served",
        lifecycle="removed",
        status_code=200,
        replacement="/v1/risk/detectors/prompt/assessments",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        service="risk-adapter",
        method="POST",
        path="/v1/risk/assessments",
        operation_id="assessRiskAggregate",
        tag="Risk Signal",
        summary="통합 risk signal",
        description=(
            "enabled detector registry 순서(pii → secret → prompt)대로 호출하고 결과를 aggregate합니다.\n\n"
            "어느 한 detector가 신호를 탐지하면 `risk_detected: true`를 반환합니다. "
            "detector 실패는 policy 판단 없이 system signal로 표현됩니다.\n\n"
            "PII Protection(D1-D3, D5)과 Secret Exposure(D4, D5) 신호를 Prompt Injection(A1, A2)과 함께 통합합니다."
        ),
        auth="internal_service",
        exposure="internal_service",
        lifecycle="stable",
        status_code=200,
        replacement=None,
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
]
