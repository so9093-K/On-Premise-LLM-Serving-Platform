from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..detectors.pii import LABELS_BY_CODE as _PII_LABELS_BY_CODE
from ..detectors.secret import LABELS_BY_CODE as _SECRET_LABELS_BY_CODE

# D-code의 사람이 읽을 이름. 코드 자체의 유효 집합은 contracts/risk.py가 갖는다.
_CODE_TITLES: dict[str, str] = {
    "D1": "Personal Identifier",
    "D2": "Contact",
    "D4": "Secret/Credential",
    "D5": "Network/Infrastructure",
}


def _detected_codes_block(labels_by_code: dict[str, tuple[str, ...]]) -> str:
    """탐지기가 실제로 내보내는 라벨 목록에서 문서의 코드 표를 만든다.

    예전에는 이 목록을 설명 문자열에 손으로 적어두고 gateway/risk-adapter 두
    군데에 복사해뒀다. 그래서 ANTHROPIC_API_KEY가 추가됐을 때 네 곳이 그대로
    뒤처졌고, OpenAPI 스냅샷 비교는 description을 보지 않아 아무도 몰랐다.
    """
    return "".join(
        f"- **{code}** {_CODE_TITLES[code]}: {', '.join(labels)}\n"
        for code, labels in sorted(labels_by_code.items())
    )


_PII_DETECTOR_DESCRIPTION = (
    "**PII Protection 탐지기** — 한국형 식별자·이메일·전화번호·카드번호·IP를 정규식으로 직접 탐지합니다.\n\n"
    "탐지 코드:\n"
    + _detected_codes_block(_PII_LABELS_BY_CODE)
    + "\n원문 PII 값은 응답에 포함되지 않습니다. `span_count`로 entity별 탐지 개수를 제공합니다.\n"
    "탐지 결과는 최종 정책 판단이 아니라 진단용 신호로 다뤄야 합니다."
)

_SECRET_DETECTOR_DESCRIPTION = (
    "**Secret Exposure 탐지기** — 정제한 정규식과 엔트로피로 직접 탐지합니다. 외부 도구 없이 프로세스 안에서 동작합니다.\n\n"
    "탐지 코드:\n"
    + _detected_codes_block(_SECRET_LABELS_BY_CODE)
    + "\n응답·로그·지표 라벨 어디에도 원문 시크릿을 남기지 않습니다. 탐지 개수는 `span_count`로 알려줍니다.\n"
    "탐지 결과는 최종 정책 판단이 아니라 진단용 신호로 다뤄야 합니다."
)


@dataclass(frozen=True)
class EndpointSpec:
    method: str         # "GET" | "POST"
    path: str
    operation_id: str
    tag: str            # OpenAPI 태그 이름
    summary: str
    description: str
    lifecycle: str      # "stable" | "retired" | "removed"
    request_schema: str | None       # 예: "chat_completion_request.schema.json"
    response_schema: str | None      # 예: "chat_completion_response.schema.json"


_SKIP_SCHEMA_LIFECYCLES: frozenset[str] = frozenset({"removed", "retired"})

RouteKey = tuple[str, str]  # (method, path)


def schema_maps_from_specs(
    endpoints: Sequence[EndpointSpec],
) -> tuple[dict[RouteKey, str], dict[RouteKey, str]]:
    """EndpointSpec 목록에서 요청·응답 스키마 매핑을 파생한다.

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
# Gateway 엔드포인트 (port 9400)
# ---------------------------------------------------------------------------

GATEWAY_ENDPOINTS: list[EndpointSpec] = [
    EndpointSpec(
        method="GET",
        path="/health",
        operation_id="getGatewayHealth",
        tag="Operations",
        summary="Liveness 확인",
        description="프로세스가 살아 있는지만 확인합니다. 항상 HTTP 200을 반환하며 인증 없이 호출할 수 있습니다.",
        lifecycle="stable",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        method="GET",
        path="/ready",
        operation_id="getGatewayReadiness",
        tag="Operations",
        summary="의존 서비스 준비 상태 확인",
        description=(
            "Gateway가 의존하는 vLLM 런타임과 Risk Adapter가 모두 요청을 받을 준비가 됐는지 확인합니다. "
            "모델 로딩 중에는 HTTP 503을 반환하며, body의 `not_ready_dependencies`에 "
            "아직 준비되지 않은 dependency 목록과 `message`가 포함됩니다."
        ),
        lifecycle="stable",
        request_schema=None,
        response_schema="readiness_response.schema.json",
    ),
    EndpointSpec(
        method="GET",
        path="/metrics",
        operation_id="getGatewayMetrics",
        tag="Monitoring",
        summary="Prometheus 지표 조회",
        description="Prometheus가 수집하는 Gateway 지표 엔드포인트입니다. 운영 환경에서는 admin 토큰 또는 내부망으로 보호합니다.",
        lifecycle="stable",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        method="GET",
        path="/internal/main-model/drain-status",
        operation_id="getMainModelDrainStatus",
        tag="Runtime Control",
        summary="메인 모델 요청 drain 상태 조회",
        description=(
            "Admin Sidecar가 모델 교체 전에 진행 중인 local-main 요청 수를 확인하는 "
            "내부 서비스 전용 endpoint입니다. OpenAPI UI에는 노출하지 않습니다."
        ),
        lifecycle="stable",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        method="GET",
        path="/v1/models",
        operation_id="listModels",
        tag="Models",
        summary="사용 가능한 모델 목록",
        description=(
            "Gateway가 외부 호출자에게 노출하는 logical model id, capability, 사용자 조정 가능 request parameter 목록입니다. "
            "catalog 성격의 엔드포인트이므로 vLLM 로딩 상태와 무관하게 항상 목록을 반환합니다. "
            "현재 serving 가능 여부는 `/ready`의 `status`와 `not_ready_dependencies`를 확인하세요. "
            "클라이언트 UI는 각 item의 `request_parameters`를 읽어 모델별 입력 form을 구성할 수 있습니다. "
            "이미지·오디오·비디오 입력의 개수·용량 한도는 `request_limits`에 modality별로 실립니다 -- "
            "조정 가능한 파라미터가 아니라 콘텐츠 제약이라 자리를 나눕니다. "
            "두 값 모두 활성 main-model 프로필을 따르므로, 클라이언트는 복제해 두지 말고 요청 시점에 읽어야 합니다."
        ),
        lifecycle="stable",
        request_schema=None,
        response_schema="model_list_response.schema.json",
    ),
    EndpointSpec(
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
        lifecycle="stable",
        request_schema="chat_completion_request.schema.json",
        response_schema="chat_completion_response.schema.json",
    ),
    EndpointSpec(
        method="POST",
        path="/v1/embeddings",
        operation_id="createEmbedding",
        tag="Embeddings",
        summary="Embedding vector 생성",
        description=(
            "`local-embed` 및 `local-embed-ko`를 통해 텍스트의 embedding vector를 생성합니다. "
            "요청 파라미터는 Gateway contract로 검증하며, 지원하지 않는 파라미터는 차단합니다."
        ),
        lifecycle="stable",
        request_schema="embedding_request.schema.json",
        response_schema="embedding_response.schema.json",
    ),
    EndpointSpec(
        method="POST",
        path="/v1/risk/detectors/prompt/assessments",
        operation_id="assessPromptRisk",
        tag="Risk",
        summary="Prompt 위협 탐지 신호",
        description=(
            "Prompt attack 탐지기의 위험 신호만 반환합니다. "
            "정책 판단 필드(`allow`, `block`, `decision` 등)는 포함되지 않으며, "
            "최종 허용·차단 결정은 Gateway 밖 product policy layer가 담당합니다."
        ),
        lifecycle="stable",
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        method="POST",
        path="/v1/risk/detectors/pii/assessments",
        operation_id="assessPIIRisk",
        tag="Risk",
        summary="PII Protection 탐지 신호",
        description=_PII_DETECTOR_DESCRIPTION,
        lifecycle="stable",
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        method="POST",
        path="/v1/risk/detectors/secret/assessments",
        operation_id="assessSecretRisk",
        tag="Risk",
        summary="Secret Exposure 탐지 신호",
        description=_SECRET_DETECTOR_DESCRIPTION,
        lifecycle="stable",
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        method="POST",
        path="/v1/risk/detectors/siren/assessments",
        operation_id="assessSirenDetector",
        tag="Risk",
        summary="Siren 탐지기 신호 (제거됨)",
        description="제거된 경로입니다. Gateway는 siren 탐지기를 제공하지 않습니다.",
        lifecycle="removed",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        method="POST",
        path="/v1/risk/assessments",
        operation_id="assessRisk",
        tag="Risk",
        summary="통합 Risk 신호",
        description=(
            "활성화된 탐지기들의 결과를 하나로 합친 통합 risk 신호입니다. "
            "PII Protection(D1, D2, D5), Secret Exposure(D4, D5), Prompt Injection(A1, A2) 신호를 통합합니다. "
            "활성화된 탐지기 중 하나라도 위험 신호를 찾으면 `risk_detected: true`를 반환합니다."
        ),
        lifecycle="stable",
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
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
        lifecycle="stable",
        request_schema="retrieval_rerank_request.schema.json",
        response_schema="retrieval_rerank_response.schema.json",
    ),
    EndpointSpec(
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
        lifecycle="stable",
        request_schema="retrieval_score_request.schema.json",
        response_schema="retrieval_score_response.schema.json",
    ),
    # ------------------------------------------------------------------ 관리자 런타임 제어
    EndpointSpec(
        method="GET",
        path="/admin/runtimes",
        operation_id="listRuntimes",
        tag="Runtime Control",
        summary="런타임 상태 조회",
        description=(
            "공유 GPU 예산 위의 모든 vLLM 런타임(보조: embedding, embedding_ko, risk_prompt; "
            "메인: `local-main`)의 현재 상태와 VRAM 점유를 반환합니다.\n\n"
            "`state`: 런타임 상태 — `active`(서비스 중) / `stopped`(컨테이너 중지, VRAM 회수) / `starting`(전환 중, 일시적).\n\n"
            "`vram_fraction`: 각 런타임의 GPU VRAM 점유율(gpu_memory_utilization).\n\n"
            "`budget`: `{ceiling, used, free}` — 활성 런타임 점유율 합과 천장.\n\n"
            "`container_status`: 실제 Docker 컨테이너 상태(참고용). 정지/시작은 보조·메인 모두 "
            "`PATCH /admin/runtimes/{service_key}`(메인은 `service_key=main`)로, "
            "메인 프로필 교체만 `POST /admin/main-model/switch`로 수행합니다."
        ),
        lifecycle="stable",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        method="PATCH",
        path="/admin/runtimes/{service_key}",
        operation_id="transitionRuntime",
        tag="Runtime Control",
        summary="런타임 상태 전환",
        description=(
            "`desired_state`에 지정한 목표 상태로 런타임을 전환합니다. "
            "`service_key`는 보조 런타임(embedding, embedding_ko, risk_prompt)과 "
            "메인 모델(`main`)을 모두 받습니다 — 함대 전체를 같은 동사로 제어합니다.\n\n"
            "- **`active`** — 컨테이너를 시작하고 gateway 라우팅을 복구합니다. "
            "이미 `active`면 no-op.\n"
            "- **`stopped`** — 컨테이너를 중지하고 GPU VRAM을 회수합니다. "
            "이미 `stopped`면 no-op.\n\n"
            "`main`은 정지 시 드레인 후 gate를 닫고, 시작 시 GPU 예산 admission과 "
            "canary 검증을 거칩니다(프로필 교체는 `POST /admin/main-model/switch`).\n\n"
            "GPU 예산을 초과하면 409와 정지 계획(`plan.stop`)을 반환하며, "
            "`force: true`로 우선순위 낮은 보조를 자동 축출할 수 있습니다. "
            "Scalar UI 드롭다운에서 선택 후 Execute만 누르면 됩니다."
        ),
        lifecycle="stable",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        method="GET",
        path="/admin/main-model",
        operation_id="getMainModel",
        tag="Runtime Control",
        summary="활성 메인 모델 조회",
        description=(
            "control-plane ledger가 기록한 상태와 Docker에서 방금 관측한 실제 런타임을 함께 반환합니다. "
            "메인 모델을 디버깅할 때 가장 먼저 보는 엔드포인트입니다.\n\n"
            "- `active_profile` — 지금 `local-main`으로 서빙 중인 프로필 전체(`capabilities.deployed_input`, "
            "`gateway_policy` 포함). chat 요청 검증에 실제로 적용되는 값이 이것입니다.\n"
            "- `gate` — `open`이면 요청을 받고, `closed`면 전환·정지 중이라 `/v1/chat/completions`가 "
            "`503 MAIN_MODEL_SWITCH_IN_PROGRESS` + `Retry-After`로 fail-closed 응답합니다.\n"
            "- `runtime_state` — ledger가 기록한 목표 상태(`active` / `stopped`).\n"
            "- `last_operation` — 가장 최근 전환 작업 요약(`status`, `stage`, `error`).\n"
            "- `observed_runtime` — Docker inspect 결과(`container_state`, `health`, `observed_at`). "
            "ledger와 어긋나면 drift이며, 이 값이 `null`이면 관측 자체가 실패한 것입니다.\n\n"
            "Docker 관측은 이 라우트에서만 수행합니다. 요청 경로(`/v1/chat/completions`, `/v1/models`)는 "
            "ledger만 읽으므로 추론이 Docker daemon 상태에 묶이지 않습니다."
        ),
        lifecycle="stable",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        method="GET",
        path="/admin/main-model/profiles",
        operation_id="listMainModelProfiles",
        tag="Runtime Control",
        summary="메인 모델 프로필 조회",
        description=(
            "이 배포에서 전환할 수 있는 메인 모델 프로필과 각 프로필의 근거를 반환합니다. "
            "`active: true`가 현재 서빙 중인 프로필입니다.\n\n"
            "- `compatibility.status` — `verified`만 추가 확인 없이 전환됩니다. 그 외 상태는 "
            "`switch` 요청에 `confirm_unverified: true`가 필요합니다.\n"
            "- `capabilities.deployed_input` — 그 프로필로 전환했을 때 받을 수 있는 입력 modality입니다. "
            "전환이 완료되면 `/v1/models`의 `input_modalities`와 chat validator에 즉시 반영됩니다.\n"
            "- `upstream_model_id`, `revision` — 실제로 로딩되는 가중치 pin입니다.\n\n"
            "프로필 목록의 source of truth는 `configs/main_model_profiles.yaml`이며, 여기 없는 ID로는 전환할 수 없습니다."
        ),
        lifecycle="stable",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        method="POST",
        path="/admin/main-model/switch",
        operation_id="switchMainModel",
        tag="Runtime Control",
        summary="메인 모델 전환",
        description=(
            "메인 모델 프로필을 비동기로 전환합니다(`202` + `operation_id`). 전환 도중 gate가 닫히고 "
            "진행 중인 요청을 drain한 뒤 컨테이너를 교체하므로, 완료까지 chat 요청은 "
            "`503 MAIN_MODEL_SWITCH_IN_PROGRESS`를 받습니다.\n\n"
            "- `profile` — `GET /admin/main-model/profiles`가 반환한 ID만 허용합니다. "
            "그 외 필드(model id, command 등)는 `422`로 거부되며 임의 실행 인자를 넣을 수 없습니다.\n"
            "- `confirm_unverified` — `compatibility.status`가 `verified`가 아닌 프로필로 전환할 때 필요합니다.\n"
            "- `request_id` — 선택적 멱등 키입니다. 진행 중이거나 방금 끝난 동일 작업이 있으면 새 전환을 "
            "시작하지 않고 그 작업을 반환하며 응답에 `reused: true`로 표시합니다(재시도 안전용이라 일정 시간 뒤 "
            "만료됩니다). 매번 새 전환을 원하면 고유한 값을 쓰거나 생략합니다.\n\n"
            "진행 상황은 `GET /admin/main-model/operations/{operation_id}` 또는 `GET /admin/main-model`의 "
            "`last_operation`으로 확인합니다. 검증 단계에서 실패하면 이전 프로필로 자동 rollback합니다."
        ),
        lifecycle="stable",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        method="GET",
        path="/admin/main-model/operations/{operation_id}",
        operation_id="getMainModelOperation",
        tag="Runtime Control",
        summary="메인 모델 전환 작업 조회",
        description=(
            "비동기 전환 작업의 현재 단계와 실패 원인을 조회합니다. `operation_id`는 "
            "`POST /admin/main-model/switch`의 `202` 응답에서 받은 값이며, `completed`·`failed`·"
            "`rollback_failed`에 도달할 때까지 폴링합니다. 각 stage의 의미는 Runtime Control 태그 설명에 있습니다.\n\n"
            "- `stage` — 지금 수행 중인 단계, `status` — 같은 값(터미널 상태에서 확정).\n"
            "- `error` — 전환을 실패시킨 원인 문자열. `rollback_error`가 함께 있으면 이전 프로필 복구까지 "
            "실패한 것이며(`rollback_failed`), 이때는 수동 개입이 필요합니다.\n"
            "- `client_request_id` — 요청에 넣은 멱등 키.\n\n"
            "가장 최근 작업은 `GET /admin/main-model`의 `last_operation`으로도 볼 수 있고, 진행 상태는 "
            "Grafana `Main-model Control` 대시보드의 Latest Operation State 패널에서 실시간으로 확인합니다."
        ),
        lifecycle="stable",
        request_schema=None,
        response_schema=None,
    ),
]

# ---------------------------------------------------------------------------
# Risk Adapter 엔드포인트 (port 9405)
# ---------------------------------------------------------------------------

RISK_ADAPTER_ENDPOINTS: list[EndpointSpec] = [
    EndpointSpec(
        method="GET",
        path="/health",
        operation_id="getRiskAdapterHealth",
        tag="Operations",
        summary="Liveness 확인",
        description="프로세스가 살아 있는지만 확인합니다. 항상 HTTP 200을 반환하며 인증 없이 호출할 수 있습니다.",
        lifecycle="stable",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        method="GET",
        path="/ready",
        operation_id="getRiskAdapterReadiness",
        tag="Operations",
        summary="Risk Adapter readiness 확인",
        description=(
            "활성화된 탐지기의 vLLM 런타임이 요청을 받을 준비가 됐는지 확인합니다. "
            "모델 로딩 중에는 HTTP 503을 반환하고 `not_ready_dependencies`와 dependency별 `message`를 제공합니다."
        ),
        lifecycle="stable",
        request_schema=None,
        response_schema="readiness_response.schema.json",
    ),
    EndpointSpec(
        method="GET",
        path="/metrics",
        operation_id="getRiskAdapterMetrics",
        tag="Monitoring",
        summary="Prometheus 지표 조회",
        description="Prometheus가 수집하는 Risk Adapter 지표입니다. 탐지기별 타임아웃, 파싱 실패, 신호 건수를 볼 수 있습니다.",
        lifecycle="stable",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        method="POST",
        path="/v1/risk/detectors/prompt/assessments",
        operation_id="assessRiskPromptDetector",
        tag="Risk Signal",
        summary="Prompt 탐지기 신호 — Prompt Injection / Leaking",
        description=(
            "**Prompt 탐지기**(`risk-prompt`)만 단독으로 호출합니다.\n\n"
            "탐지 대상:\n"
            "- system/developer instruction 무시 유도\n"
            "- 숨겨진 system prompt·tool config 출력 요구\n"
            "- 역할극(DAN 등) jailbreak\n"
            "- 문서·웹페이지 안에 숨겨진 간접 prompt injection\n"
            "- 연결된 도구로 시크릿·파일·메일 탈취 유도\n\n"
            "일반 사이버 공격 절차·폭력·혐오 콘텐츠는 이 탐지기가 다루지 않습니다."
        ),
        lifecycle="stable",
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        method="POST",
        path="/v1/risk/detectors/pii/assessments",
        operation_id="assessRiskPIIDetector",
        tag="Risk Signal",
        summary="PII Protection 탐지기 신호 — 개인정보 노출 탐지",
        description=_PII_DETECTOR_DESCRIPTION,
        lifecycle="stable",
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        method="POST",
        path="/v1/risk/detectors/secret/assessments",
        operation_id="assessRiskSecretDetector",
        tag="Risk Signal",
        summary="Secret Exposure 탐지기 신호 — 시크릿·자격증명 노출 탐지",
        description=_SECRET_DETECTOR_DESCRIPTION,
        lifecycle="stable",
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
    EndpointSpec(
        method="POST",
        path="/v1/risk/detectors/siren/assessments",
        operation_id="assessSirenDetector",
        tag="Risk Signal",
        summary="Siren 탐지기 신호 (제거됨)",
        description="제거된 경로입니다. Risk Adapter는 siren 탐지기를 제공하지 않습니다.",
        lifecycle="removed",
        request_schema=None,
        response_schema=None,
    ),
    EndpointSpec(
        method="POST",
        path="/v1/risk/assessments",
        operation_id="assessRiskAggregate",
        tag="Risk Signal",
        summary="통합 risk signal",
        description=(
            "활성화된 탐지기를 등록 순서(pii → secret → prompt)대로 호출하고 결과를 합칩니다.\n\n"
            "어느 하나라도 신호를 찾으면 `risk_detected: true`를 반환합니다. "
            "탐지기가 실패하면 정책 판단 없이 시스템 신호로만 알립니다.\n\n"
            "PII Protection(D1, D2, D5)과 Secret Exposure(D4, D5) 신호를 Prompt Injection(A1, A2)과 함께 통합합니다."
        ),
        lifecycle="stable",
        request_schema="risk_assessment_request.schema.json",
        response_schema="risk_assessment_response.schema.json",
    ),
]
