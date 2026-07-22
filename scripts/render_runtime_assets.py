#!/usr/bin/env python3
"""Runtime asset renderer.

ModelRegistry projection에서 정적 artifact를 생성한다.

생성 대상:
  ops/prometheus/prometheus.yml
  contracts/model_contracts.yaml
  specs/schemas/model_list_response.schema.json
  harness/runtime_validation_matrix.yaml
  docs/operations/full_stack_runtime.md  (generated block만 갱신)

생성 제외 대상:
  compose 파일 (full-stack.private-network.yaml):
    compose 파일에는 registry에서 파생할 수 없는 보일러플레이트가 있다
    (gateway env, prometheus secret, grafana env, healthcheck 등). PyYAML
    round-trip은 주석과 env placeholder를 유실한다.
    대신 validate_vllm_compose.py가 compose vLLM command drift를 검증하며,
    make check-runtime-assets가 이를 호출한다.

  reports/runtime/* (runtime_targets, monitoring_projection 등):
    이 파일들은 이미 make runtime-targets, make monitoring-projection 등 별도 타겟으로
    생성된다. render_runtime_assets는 이 파일들을 소유하지 않는다.

Usage:
  python scripts/render_runtime_assets.py           # dry-run (drift 보고만, exit 0)
  python scripts/render_runtime_assets.py --check   # drift 있으면 exit 1
  python scripts/render_runtime_assets.py --write   # 모든 생성 artifact 파일에 쓰기
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.domain import ModelRegistry  # noqa: E402
from ai_model_serving.errors import ERROR_STATUS  # noqa: E402
from ai_model_serving.monitoring_projection import prometheus_scrape_config_document  # noqa: E402

_GENERATED_HEADER_YAML = (
    "# 자동 생성 파일입니다. 직접 수정하지 마세요.\n"
    "# 소스: configs/model_catalog.yaml + configs/model_serving.yaml\n"
    "# 명령: make render-runtime-assets\n"
)
_GENERATED_HEADER_YAML_WITH_MONITORING = (
    "# 자동 생성 파일입니다. 직접 수정하지 마세요.\n"
    "# 소스: configs/model_catalog.yaml + configs/model_serving.yaml + configs/monitoring.yaml\n"
    "# 명령: make render-runtime-assets\n"
)

RUNTIME_TARGETS_BEGIN = "<!-- BEGIN GENERATED RUNTIME TARGETS -->"
RUNTIME_TARGETS_END = "<!-- END GENERATED RUNTIME TARGETS -->"


# ── loaders ────────────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_registry_and_monitoring(root: Path) -> tuple[ModelRegistry, dict[str, Any]]:
    registry = ModelRegistry(
        _load_yaml(root / "configs/model_catalog.yaml"),
        _load_yaml(root / "configs/model_serving.yaml"),
    )
    monitoring = _load_yaml(root / "configs/monitoring.yaml")
    return registry, monitoring


# ── renderers ──────────────────────────────────────────────────────────────────

def render_prometheus_yml(registry: ModelRegistry, monitoring: dict[str, Any]) -> str:
    doc = prometheus_scrape_config_document(registry=registry, monitoring=monitoring)
    body = yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return _GENERATED_HEADER_YAML_WITH_MONITORING + body


def render_model_contracts_yaml(registry: ModelRegistry) -> str:
    doc = registry.model_contracts_document()
    body = yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return _GENERATED_HEADER_YAML + body


def render_model_list_schema_json(registry: ModelRegistry) -> str:
    doc = registry.model_list_schema_document()
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def render_runtime_validation_matrix_yaml(registry: ModelRegistry) -> str:
    doc = registry.runtime_validation_matrix_document()
    body = yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return _GENERATED_HEADER_YAML + body


def render_error_reference_md() -> str:
    """Chat API 중심 code별 의미·retryable·조치 레퍼런스.

    입력(ERROR_STATUS, error_catalog.yaml)은 항상 실제 repo ROOT에서 읽는다(registry가
    실제 configs에서 로드되는 것과 동일). 출력 경로만 get_artifacts의 root를 따른다.
    """
    catalog = (_load_yaml(ROOT / "configs/error_catalog.yaml") or {}).get("errors", {})
    chat_error_codes = {
        "UNAUTHORIZED",
        "FORBIDDEN",
        "NOT_FOUND",
        "VALIDATION_ERROR",
        "MODEL_CAPABILITY_MISMATCH",
        "REQUEST_TOO_LARGE",
        "RATE_LIMITED",
        "MODEL_UNAVAILABLE",
        "RUNTIME_NOT_READY",
        "UPSTREAM_TIMEOUT",
        "PARSE_ERROR",
        "UPSTREAM_ERROR",
        "UPSTREAM_SCHEMA_ERROR",
        "CIRCUIT_OPEN",
        "MAIN_MODEL_CONTROL_UNAVAILABLE",
        "MAIN_MODEL_SWITCH_IN_PROGRESS",
        "QUEUE_TIMEOUT",
        "STREAM_LIMIT_EXCEEDED",
        "INTERNAL_ERROR",
    }
    by_status: dict[int, list[str]] = {}
    for code, status in ERROR_STATUS.items():
        if code not in chat_error_codes:
            continue
        by_status.setdefault(status, []).append(code)
    lines = [
        "<!-- GENERATED FILE. DO NOT EDIT MANUALLY. -->",
        "<!-- Source: src/ai_model_serving/errors.py (ERROR_STATUS) + configs/error_catalog.yaml + Chat contracts -->",
        "<!-- Command: make render-runtime-assets -->",
        "",
        "# Chat API 에러 레퍼런스",
        "",
        "`/v1/chat/completions` 실패 응답을 읽는 기준이다. 통합 에러 코드 카탈로그가 아니라 Chat API 사용자 기준 문서다.",
        "",
        "```json",
        '{ "error": { "code": "...", "message": "...", "param": "...",'
        ' "retryable": false, "request_id": "req_...", "debug": { "...": "..." } } }',
        "```",
        "",
        "| 필드 | 의미 |",
        "|---|---|",
        "| `code` | 안정적인 에러 식별자. 클라이언트 분기 기준. |",
        "| `message` | 사람이 읽는 설명. 파싱 기준으로 쓰지 않는다. |",
        "| `param` | 고쳐야 할 요청 필드. 없을 수 있다. |",
        "| `retryable` | 같은 요청 재시도 권장 여부. 실제 응답값을 우선한다. |",
        "| `request_id` | 로그 추적과 운영 문의용 ID. |",
        "| `debug` | 원본 cause/upstream 상태 요약. 운영·내부 개발자가 즉시 원인 확인에 사용한다. 없을 수 있다. |",
        "",
        "모든 에러 응답은 `code`/`request_id`/`message`를 각각 `X-Error-Code`/`X-Request-Id`/"
        "`X-Error-Message` 응답 헤더로도 그대로 반환한다"
        "(`x-request-id`를 안 보낸 요청도 에러 시 새로 발급된 request_id가 헤더로 에코되어 바디와 항상 일치한다."
        " `X-Error-Message`는 헤더 인젝션 방지를 위해 출력 가능한 ASCII만 남기고 500자로 자른 값이다)."
        " 운영 로그(구조화 접근 로그)도 이 헤더 값을 그대로 남기므로, 클라이언트가 보고한 `request_id`나"
        " `code`, 에러 메시지로 로그를 직접 검색할 수 있다.",
        "",
        "## 판단 기준",
        "",
        "| 유형 | 대표 code | 사용자/클라이언트 행동 |",
        "|---|---|---|",
        "| 요청 수정 | `VALIDATION_ERROR`, `MODEL_CAPABILITY_MISMATCH`, `REQUEST_TOO_LARGE` | `param` 기준으로 payload를 고친다. |",
        "| 인증/권한 | `UNAUTHORIZED`, `FORBIDDEN` | API key, token, 권한을 확인한다. |",
        "| 모델 준비 | `MODEL_UNAVAILABLE`, `RUNTIME_NOT_READY`, `MAIN_MODEL_*` | `Retry-After`가 있으면 기다렸다가 재시도한다. |",
        "| 모델/stream 처리 | `UPSTREAM_TIMEOUT`, `UPSTREAM_ERROR`, `UPSTREAM_SCHEMA_ERROR`, `PARSE_ERROR`, `STREAM_LIMIT_EXCEEDED` | `retryable=true`면 백오프 재시도. 반복되면 runtime/log/payload를 확인한다. |",
        "| 내부 오류 | `INTERNAL_ERROR` | `request_id`로 운영 로그를 추적한다. |",
        "",
        "## 422 구분",
        "",
        "`code`는 큰 오류 유형이다. 예를 들어 `VALIDATION_ERROR`는 요청 검증 실패를 뜻하지만,",
        "그 값만으로는 응답 형식 요청 문제인지, 메시지 구조 문제인지, 미디어 데이터 문제인지 알기 어렵다.",
        "`param`은 같은 `VALIDATION_ERROR` 안에서 실제로 고쳐야 할 요청 필드를 좁혀 주는 2차 분류다.",
        "",
        "예:",
        "",
        "```json",
        '{ "error": { "code": "VALIDATION_ERROR", "param": "response_format" } }',
        "```",
        "",
        "```json",
        '{ "error": { "code": "VALIDATION_ERROR", "param": "image_url" } }',
        "```",
        "",
        "```json",
        '{ "error": { "code": "VALIDATION_ERROR", "param": "tool_choice.function.name" } }',
        "```",
        "",
        "| param | 영역 | 예 |",
        "|---|---|---|",
        "| `response_format`, `response_format.json_schema` | 응답 형식 요청 | JSON mode/schema 요청이 잘못됨. |",
        "| `messages`, `messages[0].role`, `messages[0].content`, `messages.content.type` | 메시지 구조 | role/content/content part 구조가 잘못됨. |",
        "| `model`, `max_tokens`, `temperature`, `top_p`, `stop`, `stream_options`, `logprobs`, `top_logprobs`, `logit_bias`, `reasoning` | Chat 옵션 | 범위, 타입, 조합, 정책이 맞지 않음. |",
        "| `tools`, `tools[0].function.name`, `tool_choice`, `tool_choice.function.name`, `tool_calls` | Tool 호출 | tool schema나 선택한 tool 이름이 맞지 않음. |",
        "| `image_url`, `image_url.url`, `image_url.detail` | 이미지 입력 | URL/MIME/base64/크기/픽셀/animated image 정책 위반. |",
        "| `input_audio`, `input_audio.format` | 오디오 입력 | format/base64/크기/magic-byte/capability 문제. |",
        "| `video_url`, `video_url.url` | 비디오 입력 | URL/MIME/base64/크기/frame/capability 문제. |",
        "| 없음 | 요청 전체 | 특정 필드 하나로 귀속하기 어려움. |",
        "",
        "## 정상 요청 예시",
        "",
        "텍스트:",
        "",
        "```json",
        "{",
        '  "model": "local-main",',
        '  "messages": [',
        '    { "role": "user", "content": "안녕하세요. 요약해 주세요." }',
        "  ],",
        '  "max_tokens": 256',
        "}",
        "```",
        "",
        "이미지:",
        "",
        "```json",
        "{",
        '  "model": "local-main",',
        '  "messages": [',
        '    {',
        '      "role": "user",',
        '      "content": [',
        '        { "type": "text", "text": "이 이미지의 핵심을 설명해 주세요." },',
        '        { "type": "image_url", "image_url": { "url": "data:image/png;base64,..." } }',
        "      ]",
        "    }",
        "  ]",
        "}",
        "```",
        "",
        "오디오:",
        "",
        "```json",
        "{",
        '  "model": "local-main",',
        '  "messages": [',
        '    {',
        '      "role": "user",',
        '      "content": [',
        '        { "type": "text", "text": "이 오디오에서 들리는 내용을 요약해 주세요." },',
        '        { "type": "input_audio", "input_audio": { "format": "mp3", "data": "..." } }',
        "      ]",
        "    }",
        "  ]",
        "}",
        "```",
        "",
        "비디오:",
        "",
        "```json",
        "{",
        '  "model": "local-main",',
        '  "messages": [',
        '    {',
        '      "role": "user",',
        '      "content": [',
        '        { "type": "text", "text": "이 영상의 장면 변화를 설명해 주세요." },',
        '        { "type": "video_url", "video_url": { "url": "data:video/mp4;base64,..." } }',
        "      ]",
        "    }",
        "  ]",
        "}",
        "```",
        "",
        "JSON 응답:",
        "",
        "```json",
        "{",
        '  "model": "local-main",',
        '  "messages": [{ "role": "user", "content": "JSON으로 결과를 반환해 주세요." }],',
        '  "response_format": { "type": "json_object" }',
        "}",
        "```",
        "",
        "스트리밍:",
        "",
        "```json",
        "{",
        '  "model": "local-main",',
        '  "messages": [{ "role": "user", "content": "짧게 답해 주세요." }],',
        '  "stream": true,',
        '  "stream_options": { "include_usage": true }',
        "}",
        "```",
        "",
        "Tool calling:",
        "",
        "```json",
        "{",
        '  "model": "local-main",',
        '  "messages": [{ "role": "user", "content": "서울 날씨를 확인해 주세요." }],',
        '  "tools": [',
        '    {',
        '      "type": "function",',
        '      "function": {',
        '        "name": "get_weather",',
        '        "parameters": { "type": "object", "properties": { "city": { "type": "string" } } }',
        "      }",
        "    }",
        "  ],",
        '  "tool_choice": "auto"',
        "}",
        "```",
        "",
        "## 에러 예시",
        "",
        "`response_format.type` 값이 잘못됨:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "response_format.type is bogus; use one of: json_object, json_schema, text.",',
        '    "param": "response_format",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "`json_object`를 요청했지만 메시지에 JSON 지시가 없음:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "response_format.type=json_object requires an explicit JSON instruction in messages; add a user or system message such as \'Return JSON.\'.",',
        '    "param": "response_format",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "`messages`가 비어 있음:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "messages must be a non-empty array.",',
        '    "param": "messages",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "`messages[0].role` 값이 잘못됨:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "messages[0].role must be one of: system, user, assistant, tool.",',
        '    "param": "messages[0].role",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "`max_tokens` 범위 초과:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "max_tokens must be less than or equal to 4096.",',
        '    "param": "max_tokens",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "`stream_options`를 보냈지만 `stream=true`가 없음:",
        "",
        "```json",
        "{",
        '  "model": "local-main",',
        '  "messages": [{ "role": "user", "content": "hi" }],',
        '  "stream_options": { "include_usage": true }',
        "}",
        "```",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "stream_options may only be provided when stream=true.",',
        '    "param": "stream_options",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "`tool_choice.function.name`이 `tools` 목록에 없음:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "tool_choice.function.name is get_time; use one of the provided tools: get_weather.",',
        '    "param": "tool_choice.function.name",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "이미지 MIME이 허용되지 않음:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "image_url MIME type is image/x-icon; use one of: image/avif, image/bmp, image/gif, image/jpeg, image/jp2, image/png, image/tiff, image/webp, image/x-tiff.",',
        '    "param": "image_url",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "animated GIF를 이미지 입력으로 보냄:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "animated image/gif is not supported as image input; use video_url with data:video/gif for motion analysis.",',
        '    "param": "image_url",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "`input_audio.data`에 data URL prefix가 들어감:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "input_audio.data must be raw base64 (no data: URL prefix).",',
        '    "param": "input_audio",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "오디오 format과 실제 bytes가 맞지 않음:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "input_audio.data does not look like a valid mp3 stream; send mp3 bytes or set input_audio.format to the actual file format.",',
        '    "param": "input_audio",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "활성 모델이 오디오 입력을 지원하지 않음:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "audio content parts are not enabled for the active main model profile; remove input_audio or switch to an audio-capable profile.",',
        '    "param": "input_audio",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "비디오 MIME이 허용되지 않음:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "video_url MIME type is video/x-flv; use one of: video/avi, video/gif, video/jpeg, video/mp4, video/quicktime, video/webm, video/x-matroska, video/x-msvideo.",',
        '    "param": "video_url",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "활성 모델이 비디오 입력을 지원하지 않음:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "video content parts are not enabled for the active main model profile; remove video_url or switch to a video-capable profile.",',
        '    "param": "video_url",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "전체 JSON body가 한도를 넘음:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "REQUEST_TOO_LARGE",',
        '    "message": "Request body exceeds 100000000 bytes. Limit applies to the full JSON body, including base64 media; reduce media size or split the request.",',
        '    "retryable": false,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "upstream이 원본 400 body를 반환함:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "VALIDATION_ERROR",',
        '    "message": "Upstream rejected the request for local-main with HTTP 400; check error.debug.upstream_body for the runtime reason and adjust the request.",',
        '    "retryable": false,',
        '    "request_id": "req_...",',
        '    "debug": {',
        '      "cause_type": "HTTPStatusError",',
        '      "cause_message": "Client error 400 Bad Request",',
        '      "upstream_status": 400,',
        '      "upstream_body": "audio decoder failed"',
        "    }",
        "  }",
        "}",
        "```",
        "",
        "모델 응답이 요청한 JSON schema를 만족하지 못함:",
        "",
        "```json",
        "{",
        '  "error": {',
        '    "code": "UPSTREAM_SCHEMA_ERROR",',
        '    "message": "structured output did not match response_format.json_schema; simplify response_format.json_schema or increase max_tokens, then check error.debug for the model output reason if present.",',
        '    "retryable": true,',
        '    "request_id": "req_..."',
        "  }",
        "}",
        "```",
        "",
        "stream guard가 응답 byte 한도를 초과함:",
        "",
        "```text",
        "event: error",
        'data: {"error":{"code":"STREAM_LIMIT_EXCEEDED","message":"stream emitted 1048577 bytes; limit is 1048576. Reduce max_tokens or retry without stream=true.","retryable":false,"request_id":"req_..."}}',
        "```",
        "",
        "## Chat에서 주로 보는 code",
        "",
        "| code | HTTP | retryable | 의미 | 권장 조치 |",
        "|---|---:|:---:|---|---|",
    ]
    for status in sorted(by_status):
        for code in sorted(by_status[status]):
            meta = catalog.get(code, {}) if isinstance(catalog, dict) else {}
            retry = "✓" if meta.get("retryable") else "✗"
            meaning = str(meta.get("meaning", "")).strip()
            action = str(meta.get("action", "")).strip()
            lines.append(f"| `{code}` | {status} | {retry} | {meaning} | {action} |")
    return "\n".join(lines) + "\n"


def render_runtime_targets_block(registry: ModelRegistry) -> str:
    targets = registry.runtime_validation_targets()
    lines = [
        RUNTIME_TARGETS_BEGIN,
        "<!-- AUTO-GENERATED by scripts/render_runtime_assets.py — do not edit manually -->",
        "",
        "| service | port | logical id | served model | capabilities |",
        "|---|---:|---|---|---|",
    ]
    for t in targets:
        caps = ", ".join(f"`{c}`" for c in t.capabilities)
        lines.append(
            f"| `{t.compose_service_name}` | {t.port} | `{t.logical_id}` | `{t.served_model_name}` | {caps} |"
        )
    lines.append("")
    lines.append(RUNTIME_TARGETS_END)
    return "\n".join(lines)


# ── comparison helpers ──────────────────────────────────────────────────────────

def _strip_yaml_comments(text: str) -> str:
    """YAML 헤더 comment (#로 시작하는 줄)를 제거한다. inline comment는 대상 아님."""
    return "\n".join(line for line in text.splitlines() if not line.startswith("#"))


def compare_artifact(path: Path, expected: str) -> bool:
    """현재 파일과 expected를 semantic하게 비교한다 (comment 차이 무시).

    YAML: yaml.safe_load로 파싱 후 Python dict 비교 (필드 순서 무시).
    JSON: json.loads로 파싱 후 비교 (whitespace 무시).
    기타: 문자열 직접 비교.
    """
    if not path.exists():
        return False
    actual = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            return json.loads(actual) == json.loads(expected)
        except Exception:
            return actual == expected
    if suffix in {".yaml", ".yml"}:
        try:
            return yaml.safe_load(_strip_yaml_comments(actual)) == yaml.safe_load(
                _strip_yaml_comments(expected)
            )
        except Exception:
            return actual == expected
    return actual == expected


def compare_doc_block(path: Path, begin_marker: str, end_marker: str, block: str) -> bool:
    """doc 파일에서 begin/end marker 사이 block이 expected와 일치하는지 확인한다."""
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    begin_idx = content.find(begin_marker)
    end_idx = content.find(end_marker)
    if begin_idx == -1 or end_idx == -1:
        return False
    actual_block = content[begin_idx : end_idx + len(end_marker)]
    return actual_block.strip() == block.strip()


def patch_doc_block(content: str, begin_marker: str, end_marker: str, new_block: str) -> str:
    """begin/end marker 사이 내용(marker 포함)을 new_block으로 교체한다.

    marker가 없으면 ValueError를 발생시킨다. 문서 구조 변경 시 조용히 넘어가면
    drift를 놓칠 수 있기 때문이다.
    """
    begin_idx = content.find(begin_marker)
    end_idx = content.find(end_marker)
    if begin_idx == -1 or end_idx == -1:
        raise ValueError(
            f"generated block marker not found: {begin_marker!r} / {end_marker!r}\n"
            "문서 구조가 변경되었거나 marker가 제거된 것 같습니다."
        )
    before = content[:begin_idx]
    after = content[end_idx + len(end_marker):]
    return before + new_block + after


# ── artifact map ───────────────────────────────────────────────────────────────

def get_artifacts(
    registry: ModelRegistry, monitoring: dict[str, Any], root: Path
) -> list[tuple[Path, str]]:
    """(파일 경로, expected 내용) 목록을 반환한다."""
    return [
        (root / "ops/prometheus/prometheus.yml", render_prometheus_yml(registry, monitoring)),
        (root / "contracts/model_contracts.yaml", render_model_contracts_yaml(registry)),
        (
            root / "specs/schemas/model_list_response.schema.json",
            render_model_list_schema_json(registry),
        ),
        (
            root / "harness/runtime_validation_matrix.yaml",
            render_runtime_validation_matrix_yaml(registry),
        ),
        (root / "docs/specs/error_reference.md", render_error_reference_md()),
    ]


def get_doc_patches(
    registry: ModelRegistry, root: Path
) -> list[tuple[Path, str, str, str]]:
    """(파일 경로, begin_marker, end_marker, block_content) 목록을 반환한다."""
    return [
        (
            root / "docs/operations/full_stack_runtime.md",
            RUNTIME_TARGETS_BEGIN,
            RUNTIME_TARGETS_END,
            render_runtime_targets_block(registry),
        ),
    ]


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="ModelRegistry projection에서 runtime artifact를 생성/검증합니다."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write", action="store_true", help="생성 산출물을 실제 파일에 씁니다."
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="현재 파일과 expected를 비교하고 drift가 있으면 exit 1합니다.",
    )
    parser.add_argument("--root", default=str(ROOT), help="프로젝트 루트 경로")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    registry, monitoring = _load_registry_and_monitoring(root)
    artifacts = get_artifacts(registry, monitoring, root)
    doc_patches = get_doc_patches(registry, root)

    if args.write:
        for path, content in artifacts:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(root)}")
        for path, begin_marker, end_marker, block in doc_patches:
            if not path.exists():
                print(f"skip (not found): {path.relative_to(root)}", file=sys.stderr)
                continue
            current = path.read_text(encoding="utf-8")
            try:
                updated = patch_doc_block(current, begin_marker, end_marker, block)
            except ValueError as exc:
                print(f"error: {path.relative_to(root)}: {exc}", file=sys.stderr)
                return 1
            path.write_text(updated, encoding="utf-8")
            print(f"patched {path.relative_to(root)}")
        return 0

    # check 또는 dry-run 모드
    drifts: list[str] = []
    for path, expected in artifacts:
        if not compare_artifact(path, expected):
            drifts.append(str(path.relative_to(root)))
    for path, begin_marker, end_marker, block in doc_patches:
        if not compare_doc_block(path, begin_marker, end_marker, block):
            drifts.append(f"{path.relative_to(root)} (generated block)")

    if drifts:
        print("Runtime asset drift detected:", file=sys.stderr)
        for d in drifts:
            print(f"  {d}", file=sys.stderr)
        print("Run: make render-runtime-assets  to update.", file=sys.stderr)
        if args.check:
            return 1
        # dry-run: drift 보고만 하고 exit 0
        return 0

    print("All runtime assets are up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
