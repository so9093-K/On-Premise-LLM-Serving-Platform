from __future__ import annotations

from typing import Any

from .main_model.control import OPERATION_STAGES
from .settings_parts.types import AppSettings

# ---------------------------------------------------------------------------
# Gateway
#
# 문서 화면(Scalar `/docs`)에 보이는 모델 표·한도·파라미터 목록은 손으로 적지 않고
# 전부 AppSettings에서 만든다. 같은 값이 `/v1/models` 응답과 요청 검증에도 쓰이므로,
# configs를 고치면 문서·계약·검증이 한꺼번에 따라온다 -- 문서에만 숫자를 복사해 두면
# 프로필을 하나 바꿀 때 문서가 조용히 거짓말을 하게 된다.
# ---------------------------------------------------------------------------


def _codes(values: Any) -> str:
    """값 목록을 코드 표기로 나열한다. 비어 있으면 대시를 돌려준다."""
    items = [str(value) for value in values or ()]
    return ", ".join(f"`{item}`" for item in items) if items else "—"


def _bytes(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "—"
    if number < 1_048_576:
        return f"{number:,} bytes"
    return f"{number:,} bytes ({number / 1_048_576:.1f} MiB)"


def _number(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _main_model_parameter(settings: AppSettings, name: str) -> dict[str, Any]:
    """공개 목록에서 메인 모델의 파라미터 정의 하나를 꺼낸다(없으면 빈 dict)."""
    main_model_id = settings.runtime("main_llm").model
    for model in settings.public_models:
        if model.get("id") == main_model_id:
            definition = (model.get("request_parameters") or {}).get(name)
            return definition if isinstance(definition, dict) else {}
    return {}


def _models_tag_description(settings: AppSettings) -> str:
    lines = [
        "Gateway가 외부 호출자에게 노출하는 logical model catalog입니다. "
        "아래 표는 이 배포의 설정에서 생성되며, 런타임 권위는 `GET /v1/models` 응답입니다.",
        "",
        "| 모델 | backend | 입력 modality | capability | 조정 가능 파라미터 |",
        "|---|---|---|---|---|",
    ]
    for model in settings.public_models:
        parameters = model.get("request_parameters") or {}
        fixed = model.get("fixed_parameters") or {}
        if parameters:
            parameter_cell = f"{len(parameters)}개 — `request_parameters` 참고"
        elif fixed:
            parameter_cell = "없음 (고정값 사용)"
        else:
            parameter_cell = "없음"
        lines.append(
            "| `{id}` | `{backend}` | {modalities} | {capabilities} | {parameters} |".format(
                id=model.get("id", "—"),
                backend=model.get("backend", "—"),
                modalities=_codes(model.get("input_modalities")),
                capabilities=_codes(model.get("capabilities")),
                parameters=parameter_cell,
            )
        )
        if fixed:
            lines.append(
                "| | | | 고정 파라미터 | "
                + ", ".join(f"`{key}={value}`" for key, value in fixed.items())
                + " |"
            )
    lines += [
        "",
        "`request_parameters`는 파라미터마다 타입과 허용 범위(`min`, `max`, `max_items`, `allowed` 등)를 "
        "함께 반환합니다. 모델별 입력 form UI는 이 값을 읽어 구성해야 하며, 이 문서 화면의 예시 값은 "
        "client preset일 뿐 Gateway 기본값이 아닙니다.",
    ]

    if settings.main_model_profile_summaries:
        lines += [
            "",
            "### `local-main`은 프로필 façade입니다",
            "",
            "`local-main`은 고정된 하나의 모델이 아니라 현재 활성화된 메인 모델 프로필의 공개 이름입니다. "
            "프로필을 전환하면 입력 modality, 토큰 한도, 파라미터 allowlist가 함께 바뀌고 `/v1/models` 응답이 "
            "즉시 그 값을 반영합니다.",
            "",
            "| 프로필 ID | 이름 | 호환성 |",
            "|---|---|---|",
        ]
        for profile_id, display_name, status in settings.main_model_profile_summaries:
            lines.append(f"| `{profile_id}` | {display_name} | `{status or 'unknown'}` |")
        lines += [
            "",
            "호환성이 `verified`가 아닌 프로필로 전환하려면 `POST /admin/main-model/switch`에 "
            "`confirm_unverified: true`가 필요합니다. 각 프로필의 근거는 "
            "`GET /admin/main-model/profiles`가 반환합니다.",
        ]
    return "\n".join(lines)


def _chat_tag_description(settings: AppSettings) -> str:
    policy = settings.default_main_model_gateway_policy or {}
    limits = policy.get("request_limits") or {}
    parameters = policy.get("request_parameter_policy") or {}

    lines = [
        "OpenAI 호환 chat completions API입니다. Gateway는 upstream으로 넘기기 전에 model id, 입력 modality, "
        "토큰 한도, 파라미터 allowlist, 구조화 출력 스키마를 직접 검증합니다.",
    ]
    if not policy:
        lines.append(
            "\n기능별 한도는 활성 메인 모델 프로필이 결정하며 `GET /v1/models`의 `request_parameters`가 권위입니다."
        )
        return "\n".join(lines)

    lines += [
        "",
        "아래 값은 이 배포의 **기본 프로필** 정책입니다. 프로필을 전환하면 값이 달라지므로, "
        "실행 시점의 권위는 `GET /v1/models`(사용자용)와 `GET /admin/main-model`의 `active_profile.gateway_policy`"
        "(운영자용)입니다.",
        "",
        "### 요청 파라미터 allowlist",
        "",
        f"- 허용 파라미터: {_codes(parameters.get('supported_parameters'))}",
    ]
    if parameters.get("allow_unlisted_parameters") is False:
        lines.append(
            "- 목록에 없는 파라미터는 upstream에 전달되지 않고 `422 VALIDATION_ERROR`로 거부됩니다. "
            "응답 `error.param`이 문제가 된 필드명을 가리킵니다."
        )
    lines.append(
        "- `max_completion_tokens`는 OpenAI가 `max_tokens`를 대체한 이름입니다. 같은 한도를 가리키므로 "
        "요청은 두 이름 중 하나만 담아야 하고, upstream 전에 `max_tokens`로 접힙니다."
    )
    dropped = [str(name) for name in parameters.get("drop_upstream_parameters", [])]
    if dropped:
        lines.append(
            f"- 다음 파라미터는 Gateway 계약에서만 쓰이고 런타임에는 전달되지 않습니다: {_codes(dropped)}. "
            "응답과 metric label에 영향이 없습니다."
        )
    lines.append(
        "- 메시지 역할은 `system`, `developer`, `user`, `assistant`(+ tool 지원 시 `tool`)입니다. "
        "`developer`는 OpenAI가 `system`을 대체한 이름이며 동일하게 처리됩니다."
    )
    lines += [
        "",
        "### 토큰 한도",
        "",
        f"- `max_model_len` {_number(limits.get('max_model_len'))} — 입력과 출력 토큰의 합에 대한 상한입니다.",
        f"- `max_tokens` 상한 {_number(policy.get('max_output_tokens'))} — 요청이 이 값을 넘으면 "
        "`422 VALIDATION_ERROR`(`error.param: max_tokens`)입니다.",
        f"- `n` 최대 {_number(parameters.get('max_n'))} — 한 요청이 만들 수 있는 choice 개수입니다.",
        f"- 요청 본문 자체의 상한은 {_bytes(settings.max_request_body_bytes)}입니다"
        "(base64 미디어 포함). 초과하면 `413 REQUEST_TOO_LARGE`입니다.",
        "",
        "### 스트리밍",
        "",
        "- `stream: true` — upstream SSE chunk를 버퍼링 없이 `text/event-stream`으로 중계합니다.",
        "- `stream_options.include_usage: true` — 마지막 chunk에 token usage를 포함합니다.",
        f"- 스트림 상한: 최대 {_number(settings.streaming_max_duration_seconds)}초, "
        f"{_number(settings.streaming_max_chunks)} chunk, {_bytes(settings.streaming_max_bytes)}.",
        "- 전송 도중 오류가 나면 이미 `200`으로 헤더가 나간 뒤이므로 상태 코드를 바꿀 수 없습니다. "
        "Gateway는 SSE `error` 이벤트를 먼저 보내고 `data: [DONE]`으로 스트림을 닫으므로, "
        "클라이언트는 마지막 이벤트를 반드시 확인해야 합니다.",
    ]

    tool_calling = parameters.get("tool_calling") or {}
    if tool_calling:
        lines += [
            "",
            "### 도구 호출 (tools)",
            "",
            f"- 지원 여부: `{str(tool_calling.get('enabled', False)).lower()}` "
            "— 비활성 프로필에서는 `tools`를 보내면 거부됩니다.",
            f"- `tools` 최대 {_number(tool_calling.get('max_tools'))}개.",
            f"- `parallel_tool_calls` 허용: `{str(tool_calling.get('allow_parallel_tool_calls', False)).lower()}` "
            "— 허용되지 않으면 `true`를 보낼 수 없습니다.",
            f"- `tool_choice`: {_codes(_main_model_parameter(settings, 'tool_choice').get('allowed'))} "
            "또는 함수 지정 객체.",
            "- 프로필이 tool calling을 지원하지 않으면 `/v1/models`의 capability 목록에서 "
            "`chat.completions.tools`가 빠집니다.",
        ]

    response_format = parameters.get("response_format") or {}
    if response_format:
        json_schema = response_format.get("json_schema") or {}
        json_object = response_format.get("json_object") or {}
        lines += [
            "",
            "### 구조화 출력 (response_format)",
            "",
            f"- 허용 타입: {_codes(response_format.get('types'))}",
        ]
        if json_object.get("require_json_instruction"):
            lines.append(
                "- `json_object` — JSON mode입니다. `messages`에 JSON으로 답하라는 명시적 지시문이 있어야 하며, "
                "특정 스키마와의 일치는 보장하지 않습니다."
            )
        if json_schema:
            lines += [
                "- `json_schema` — Structured Outputs입니다. 다음 제약을 Gateway가 미리 검사하고, "
                "위반하면 upstream 호출 없이 `422`로 거부합니다.",
                f"  - root는 `object`여야 함: `{str(json_schema.get('require_root_object', False)).lower()}`",
                f"  - `additionalProperties: false` 필수: "
                f"`{str(json_schema.get('require_additional_properties_false', False)).lower()}`",
                f"  - 스키마 크기 {_bytes(json_schema.get('max_schema_bytes'))}, 최대 깊이 "
                f"{_number(json_schema.get('max_depth'))}, 전체 property "
                f"{_number(json_schema.get('max_total_properties'))}개",
                "  - optional 필드는 nullable union(`[\"type\", \"null\"]`)으로 표현하고, external `$ref`는 쓸 수 없습니다.",
                "- **열린 값 타입에는 경계를 주세요.** `integer`/`number`에 `minimum`·`maximum`, "
                "자유 `string`에 `maxLength` 또는 `pattern`이 없으면 문법상 값이 무한히 이어질 수 있어, "
                "모델이 닫는 토큰을 내지 못하고 `max_tokens`에서 잘립니다(자릿수·소수점 반복). "
                "경계가 있으면 문법 자체가 닫히므로 이 실패가 생기지 않습니다.",
                "- 생성 결과가 스키마를 만족하지 못하면 `UPSTREAM_SCHEMA_ERROR`(retryable)입니다. "
                "위 경계를 먼저 확인하고, 그다음 스키마를 단순화하거나 `max_tokens`를 늘리세요.",
            ]

    reasoning = parameters.get("reasoning") or {}
    if reasoning:
        lines += [
            "",
            "### 추론 (reasoning)",
            "",
            f"- 지원 여부: `{str(reasoning.get('enabled', False)).lower()}`, 기본값 "
            f"`{str(reasoning.get('default', False)).lower()}`, 방식 `{reasoning.get('mode', '—')}`.",
            "- 요청마다 `reasoning: true`로 켜는 opt-in이며, 켜면 출력 토큰을 더 씁니다 — "
            "`max_tokens` 여유를 함께 늘려야 답변이 잘리지 않습니다.",
        ]

    lines += [
        "",
        "### 진단용 파라미터",
        "",
        "- `logprobs` / `top_logprobs` — 토큰별 확률을 응답에 포함합니다. `top_logprobs`는 `logprobs: true`가 전제입니다.",
        "- `logit_bias` — token id 기준으로 편향을 겁니다. token id는 **활성 프로필의 tokenizer 기준**이므로, "
        "프로필을 전환하면 같은 id가 다른 토큰을 가리킬 수 있습니다.",
        "- `seed` — 같은 입력·같은 프로필에서 재현성을 높입니다(완전 결정성을 보장하지는 않습니다).",
    ]

    if limits.get("input_modalities"):
        lines += [
            "",
            "### 멀티모달 입력",
            "",
            f"현재 기본 프로필이 받는 입력: {_codes(limits.get('input_modalities'))}. "
            "선언되지 않은 modality의 content part를 보내면 `422 VALIDATION_ERROR`로 거부되고, "
            "`error.param`이 어떤 part였는지 알려줍니다(`messages.content`, `image_url`, `input_audio`, `video_url`).",
            "",
            "| 항목 | 이미지 | 오디오 | 비디오 |",
            "|---|---|---|---|",
            "| 최대 개수 | {} | {} | {} |".format(
                _number(limits.get("max_image_inputs")),
                _number(limits.get("max_audio_inputs")),
                _number(limits.get("max_video_inputs")),
            ),
            "| 최대 크기 | {} | {} | {} |".format(
                _bytes(limits.get("max_image_bytes")),
                _bytes(limits.get("max_audio_bytes")),
                _bytes(limits.get("max_video_bytes")),
            ),
            "| 허용 형식 | {} | {} | {} |".format(
                _codes(limits.get("allowed_image_mime_types")),
                _codes(limits.get("allowed_audio_formats")),
                _codes(limits.get("allowed_video_mime_types")),
            ),
            "| URL scheme | {} | 인라인 base64 | {} |".format(
                _codes(limits.get("allowed_image_url_schemes")),
                _codes(limits.get("allowed_video_url_schemes")),
            ),
            "",
            f"- 이미지 픽셀 상한 {_number(limits.get('max_image_pixels'))}px — 압축 폭탄을 막기 위해 "
            "디코딩 전에 헤더에서 해상도를 읽어 검사하며, 해상도를 읽지 못하면 거부합니다(fail-closed).",
        ]
        if limits.get("max_video_frames") or limits.get("max_video_duration_seconds"):
            lines.append(
                f"- 비디오는 최대 {_number(limits.get('max_video_frames'))} 프레임, "
                f"{_number(limits.get('max_video_duration_seconds'))}초, 프레임당 "
                f"{_number(limits.get('max_video_frame_pixels'))}px까지 허용합니다."
            )
        lines.append(
            "- 크기·형식 위반도 같은 방식으로 `error.param`이 원인을 가리킵니다"
        "(예: `input_audio.format`은 형식 문제, `image_url`은 이미지 자체의 문제)."
        )
    return "\n".join(lines)


def _embeddings_tag_description(settings: AppSettings) -> str:
    lines = [
        "텍스트 embedding API입니다. 모델마다 출력 차원이 고정되어 있고, Gateway가 요청 차원과 "
        "응답 차원이 일치하는지 확인합니다.",
    ]
    if settings.embedding_profiles:
        lines += [
            "",
            "| 모델 | 기본 차원 | 허용 차원 | encoding_format |",
            "|---|---|---|---|",
        ]
        for model_id, profile in settings.embedding_profiles.items():
            policy = profile.request_parameter_policy or {}
            lines.append(
                "| `{model}` | {default} | {dims} | {formats} |".format(
                    model=model_id,
                    default=f"`{profile.default_dimensions}`",
                    dims=_codes(policy.get("dimensions")),
                    formats=_codes(policy.get("encoding_formats", ["float"])),
                )
            )
        lines += [
            "",
            "- `base64`는 little-endian float32 배열을 인코딩한 문자열로 반환됩니다. openai-python은 "
            "numpy가 설치되어 있으면 이 형식을 기본으로 요청하므로, 형식을 지정하지 않은 공식 SDK 호출도 "
            "그대로 동작합니다.",
            "- `input`은 문자열 또는 비어 있지 않은 문자열 배열입니다. token id 배열 입력은 받지 않습니다.",
        ]
    return "\n".join(lines)


def _runtime_control_tag_description(settings: AppSettings) -> str:
    stage_meanings = {
        "pending": "작업 접수, 아직 시작 전",
        "preparing": "이미지·가중치 등 사전 준비",
        "draining": "gate를 닫고 진행 중인 요청이 끝나기를 대기",
        "stopping": "이전 런타임 컨테이너 정지",
        "starting": "새 프로필 컨테이너 기동",
        "validating": "기동한 런타임에 canary 요청으로 계약 확인",
        "rolling_back": "검증 실패로 이전 프로필 복구 중",
        "completed": "전환 성공, gate 재개방 (터미널)",
        "failed": "전환 실패, 이전 프로필로 복구됨 (터미널)",
        "rollback_failed": "복구까지 실패 — 수동 개입 필요 (터미널)",
    }
    lines = [
        "GPU 예산을 공유하는 vLLM 런타임 함대를 제어하는 관리자 API입니다. admin token이 필요합니다.",
        "",
        "### 제어 모델",
        "",
        "- **정지·시작**은 보조 런타임과 메인 모델 모두 `PATCH /admin/runtimes/{service_key}`의 "
        "`desired_state`(`active` / `stopped`) 하나로 수행합니다. 메인 모델의 `service_key`는 `main`입니다.",
        "- **메인 모델의 프로필 교체**만 `POST /admin/main-model/switch`로 따로 수행합니다.",
        "- 모든 런타임은 같은 VRAM 예산(`budget: {ceiling, used, free}`)을 나눠 씁니다. 시작 요청이 천장을 "
        "넘으면 `409 GPU_BUDGET_EXCEEDED`와 함께 `error.details.plan.stop`에 정지 후보를 돌려주며, "
        "`force: true`로 우선순위가 낮은 보조 런타임을 자동 축출할 수 있습니다.",
        "",
        "### gate — 요청 경로와의 관계",
        "",
        "- `gate: open`이어야 `/v1/chat/completions`가 요청을 받습니다.",
        "- 전환·정지 중에는 gate가 닫히고 chat 요청은 `503 MAIN_MODEL_SWITCH_IN_PROGRESS` + `Retry-After: 5`로 "
        "fail-closed 응답합니다(요청이 조용히 잘못된 모델로 가지 않습니다).",
        "- 요청 경로는 gate와 활성 프로필을 control-plane ledger에서만 읽습니다. Docker 관측은 "
        "`GET /admin/main-model`에서만 수행하므로, 추론 트래픽이 Docker daemon 상태에 묶이지 않습니다.",
        "",
        "### 전환 작업 stage",
        "",
        "`POST /admin/main-model/switch`는 즉시 `202`와 `operation_id`를 돌려주고 아래 순서로 진행합니다. "
        "`GET /admin/main-model/operations/{operation_id}`로 폴링합니다.",
        "",
        "| stage | 의미 |",
        "|---|---|",
    ]
    for stage in OPERATION_STAGES:
        lines.append(f"| `{stage}` | {stage_meanings.get(stage, '')} |")
    lines += [
        "",
        "`stopping` 이후는 이전 런타임이 이미 해체된 뒤라, 실패하면 gate를 그냥 다시 여는 대신 이전 프로필로 "
        "rollback합니다. rollback까지 실패하면 `rollback_failed`로 남고 이때만 수동 개입이 필요합니다.",
    ]
    if settings.main_model_profile_summaries:
        lines += [
            "",
            "전환 가능한 프로필: "
            + ", ".join(
                f"`{profile_id}`" for profile_id, _display, _status in settings.main_model_profile_summaries
            )
            + ". 근거와 활성 여부는 `GET /admin/main-model/profiles`가 반환합니다.",
        ]
    return "\n".join(lines)


def gateway_tags_metadata(settings: AppSettings) -> list[dict[str, str]]:
    """Gateway OpenAPI 태그 설명을 만든다. 모델·한도 값은 settings에서 나온다."""
    return [
        {
            "name": "Operations",
            "description": _OPERATIONS_TAG,
        },
        {
            "name": "Monitoring",
            "description": "Prometheus scrape용 metric 엔드포인트입니다. 운영 환경에서는 admin token 또는 내부망으로 보호합니다.",
        },
        {
            "name": "Models",
            "description": _models_tag_description(settings),
        },
        {
            "name": "Chat",
            "description": _chat_tag_description(settings),
        },
        {
            "name": "Embeddings",
            "description": _embeddings_tag_description(settings),
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
                "응답이 HTTP 200이어도 `status=failed` 또는 `assessment_complete=false`이면 detector 실패입니다. 이 경우 `risk_detected=false`를 안전 판정으로 해석하지 마세요.\n\n"
                "**Sensitive Data Protection** — PII Protection + Secret Exposure Signal:\n"
                "- **PII Protection** (D1, D2, D5): 주민등록번호, 이메일, 전화번호, IP 주소 탐지\n"
                "- **Secret Exposure** (D4, D5): API 키, JWT, private key, 비밀번호, DB URL 탐지\n\n"
                "**Prompt detector** (`risk-prompt`) — Prompt Injection / Prompt Leaking 탐지:\n"
                "- system/developer instruction 무시 유도\n"
                "- 숨겨진 system prompt 출력 요구\n"
                "- 역할극(DAN, unrestricted AI 등) jailbreak\n"
                "- 문서·웹페이지 안에 숨겨진 간접 prompt injection\n"
                "- 연결된 도구로 시크릿·파일·메일 탈취 유도"
            ),
        },
        {
            "name": "Runtime Control",
            "description": _runtime_control_tag_description(settings),
        },
    ]


_OPERATIONS_TAG = """`/health`는 process liveness, `/ready`는 vLLM과 Risk Adapter 전체 dependency 상태를 확인합니다.

문제가 생긴 요청 하나를 끝까지 추적하는 방법도 여기에 정리했습니다.

### 요청 추적 헤더

| 헤더 | 언제 | 내용 |
|---|---|---|
| `X-Request-Id` | 오류 응답 | 요청 추적 키 |
| `X-Error-Code` | 오류 응답 | `error.code` |
| `X-Error-Message` | 오류 응답 | 사람이 읽는 원인 설명 |
| `Retry-After` | 재시도 가능한 429/503 | 다음 재시도까지 기다릴 초 |

- `X-Request-Id` — 요청에 직접 보냈으면 그 값, 안 보냈으면 Gateway가 발급한 `req_<hex>`.
  접근 로그의 `request_id`와 항상 같습니다. 호출할 때 붙이면(최대 128자) 클라이언트 로그와
  서버 로그를 같은 키로 맞출 수 있습니다.
- `X-Error-Code` — 같은 HTTP status에 여러 code가 몰립니다. 예를 들어 `503`은
  `MODEL_UNAVAILABLE`, `QUEUE_TIMEOUT`, `CIRCUIT_OPEN`, `MAIN_MODEL_SWITCH_IN_PROGRESS`가
  모두 쓰므로 status만으로 원인을 나누면 안 됩니다.
- `X-Error-Message` — 출력 가능한 ASCII, 500자 제한.
- `Retry-After` — 올림 처리되므로 최소 `1`입니다.

### 오류 본문

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "...",
    "retryable": false,
    "request_id": "req_...",
    "param": "response_format.json_schema",
    "debug": {"cause_type": "...", "cause_message": "..."},
    "details": {"plan": {"stop": ["embedding"]}}
  }
}
```

- `code` / `retryable` — `retryable`은 code로 완전히 결정됩니다. 같은 code가 호출마다 다른 값으로
  나가는 일은 없으므로 재시도 로직은 code만 보고 판단하면 됩니다.
- `param` — 문제가 된 요청 필드명입니다. 메시지 문자열을 파싱하지 말고 이 값을 쓰세요
  (예: `max_tokens` vs `input_audio.format`).
- `debug` — 원인 예외 요약(`cause_type`, `cause_message`, upstream 상태). 길이 제한이 걸린
  요약이며 원문 응답 본문이 아닙니다.
- `details` — code만으로 표현할 수 없는 구조화된 복구 정보입니다(예: `GPU_BUDGET_EXCEEDED`의 `plan.stop`).

각 엔드포인트의 status별 응답 설명에 그 status에서 나올 수 있는 code와 의미·대응이 함께 적혀 있습니다.

### 서버 쪽에서 확인할 것

접근 로그는 요청 한 건당 `request_id`, `route`, `status_code`, `latency_ms`, `error_code`,
`error_retryable`, `error_cause_type`, `error_cause_message`, `error_upstream_status`,
`prompt_tokens` / `completion_tokens` / `total_tokens`를 남깁니다.
`INTERNAL_ERROR`는 클라이언트에게 고정 문구만 나가지만 로그에는 원인이 함께 남습니다.

`LOG_REQUEST_RESPONSE_BODY=true`일 때만 chat 요청·응답 본문 프리뷰가 로그에 추가되며,
PII·시크릿은 마스킹된 뒤 기록됩니다.

### 증상별 확인 순서

| 증상 | 먼저 볼 것 |
|---|---|
| 503이 계속 난다 | `X-Error-Code` → 아래 참고 |
| 422로 거부된다 | `error.param`이 가리키는 필드를 `GET /v1/models`의 `request_parameters`와 대조 |
| 기능을 지원하지 않는다고 한다 | `GET /v1/models`의 `capabilities`·`input_modalities` |
| 스트리밍이 도중에 끊긴다 | 마지막 SSE 이벤트(오류는 `error` 뒤 `[DONE]`) |
| 응답이 잘린다 | `max_tokens`, 프로필의 `max_model_len`, `reasoning` 사용 여부 |

503은 `X-Error-Code`로 갈립니다.

- `MAIN_MODEL_SWITCH_IN_PROGRESS` — `GET /admin/main-model`의 `gate`와 `last_operation`
- `MODEL_UNAVAILABLE` — `GET /admin/runtimes`의 `state`

`capabilities`·`input_modalities`는 활성 프로필이 바뀌면 함께 바뀝니다.
"""


def gateway_description(settings: AppSettings) -> str:
    """Gateway OpenAPI `info.description`(문서 첫 화면)을 만든다.

    첫 화면은 "어떻게 처음 호출하나"와 "인증" 두 가지만 다룬다. 엔드포인트·태그 목록은
    왼쪽 사이드바가 summary와 함께 이미 보여주므로 여기서 다시 나열하지 않고, 장애 대응
    레퍼런스는 Operations 태그(`/health`·`/ready` 옆)에 둔다. 예전에는 이 세 가지가 한
    화면에 섞여 있어서, 목차처럼 보이지만 실제로는 사이드바 사본 + 트러블슈팅 매뉴얼이었다.
    """
    return f"""
vLLM 기반 LLM·Embedding·Risk 런타임을 하나의 OpenAI 호환 API로 제공합니다.
문서에 보이는 한도와 파라미터 목록은 이 배포의 실제 설정에서 생성되며, 실행 시점의 권위는
`GET /v1/models`입니다.

## 첫 호출

`$GATEWAY`는 이 문서를 열고 있는 주소입니다.

```bash
curl -X POST "$GATEWAY/v1/chat/completions" \\
  -H "Authorization: Bearer $API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "model": "{settings.runtime('main_llm').model}",
    "messages": [{{"role": "user", "content": "안녕하세요"}}]
  }}'
```

응답은 OpenAI chat completion 형식입니다. 사용할 수 있는 모델과 조정 가능한 파라미터는
`GET /v1/models`의 `request_parameters`가 알려줍니다.

## 인증

- **bearerAuth** — `/v1/*` 사용자 API: `Authorization: Bearer <API_KEY>`
- **adminBearerAuth** — `/ready`, `/metrics`, `/admin/*`: `Authorization: Bearer <ADMIN_API_KEY>`
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
            "HTTP 200 응답의 `status=failed` 또는 `assessment_complete=false`는 detector 실패이며, `risk_detected=false`만으로 안전 판정하면 안 됩니다.\n\n"
            "**Sensitive Data Protection**:\n"
            "- **PII Protection** (D1, D2, D5) — 로컬 정규식 기반 개인정보 탐지\n"
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
| **PII Protection** | local (regex) | 개인정보 노출 | D1, D2, D5 |
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
