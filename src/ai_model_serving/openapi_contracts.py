from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fastapi import FastAPI
import yaml

ContractRouteKey = tuple[str, str]
SchemaMap = Mapping[ContractRouteKey, str]
ExamplesMap = Mapping[ContractRouteKey, dict[str, Any]]


STANDARD_ERROR_DESCRIPTIONS: dict[str, str] = {
    "401": "인증 실패",
    "413": "요청 body가 너무 큼",
    "422": "요청 검증 실패",
    "429": "Upstream rate limit 또는 local admission timeout",
    "500": "내부 서버 오류",
    "502": "Upstream 오류 또는 유효하지 않은 upstream 응답",
    "503": "Runtime 또는 dependency를 사용할 수 없음",
    "504": "Upstream timeout",
}

POST_STANDARD_ERROR_CODES = ("401", "413", "422", "429", "500", "502", "503", "504")


def _status_to_codes() -> dict[str, list[str]]:
    """상태별 오류 코드 enum을 위해 ``ERROR_STATUS``를 HTTP status 기준으로 역변환한다."""
    from .errors import ERROR_STATUS

    mapping: dict[str, list[str]] = {}
    for code, status in ERROR_STATUS.items():
        mapping.setdefault(str(status), []).append(code)
    return mapping


def _load_error_catalog() -> dict[str, dict[str, Any]]:
    """API 오류 가이드를 렌더링하는 데 필요한 오류 catalog를 읽는다."""
    try:
        root = find_project_root()
        data = yaml.safe_load((root / "configs" / "error_catalog.yaml").read_text(encoding="utf-8"))
        errors = data.get("errors", {}) if isinstance(data, dict) else {}
        if not isinstance(errors, dict) or not errors:
            raise ValueError("errors must be a non-empty mapping")
        return errors
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise RuntimeError("cannot load configs/error_catalog.yaml required for OpenAPI error guidance") from exc


def _error_code_gloss(codes: list[str], catalog: dict[str, dict[str, Any]]) -> str:
    """상태별 오류 코드의 의미와 재시도 가능 여부를 담은 Markdown 설명을 만든다.

    Scalar/Swagger render the response description as markdown, so a reader sees the
    exact status -> code -> meaning mapping instead of the full code enum on every
    response.
    """
    from .errors import ERROR_RETRYABLE

    lines = []
    for code in sorted(codes):
        meta = catalog.get(code, {})
        meaning = str(meta.get("meaning", "")).strip()
        # retryable은 code로 완전히 결정되며 ERROR_RETRYABLE이 런타임 권위다. 예전엔
        # error_catalog.yaml이 같은 값을 한 벌 더 들고 있었고, 문서(이 gloss)는 YAML을,
        # 실제 응답은 ERROR_RETRYABLE을 읽었다 -- 둘이 갈라지면 API는 "재시도하라"고
        # 하면서 문서는 반대로 적는 상태가 된다. 이제 양쪽 모두 여기 한 곳을 읽는다.
        retry = "재시도 가능" if ERROR_RETRYABLE.get(code) else "재시도 불가"
        lines.append(f"- `{code}` — {meaning} ({retry})" if meaning else f"- `{code}` ({retry})")
    return "\n\n이 status로 올 수 있는 `error.code`:\n" + "\n".join(lines)


def _narrowed_error_schema(
    common_error_schema: dict[str, Any], status: str, status_codes: dict[str, list[str]]
) -> dict[str, Any]:
    """공통 오류 스키마를 복사해 해당 status가 반환하는 코드로 enum을 좁힌다.

    Keeps title and every other property (incl. ``param``) so contract tests that
    assert ``CommonErrorResponse`` still pass; only the ``code`` enum is scoped.
    """
    schema = copy.deepcopy(common_error_schema)
    allowed = status_codes.get(status)
    if allowed:
        try:
            schema["properties"]["error"]["properties"]["code"]["enum"] = sorted(allowed)
        except (KeyError, TypeError):
            pass
    return schema


def _inject_standard_error_responses(document: dict[str, Any], common_error_schema: dict[str, Any]) -> None:
    """생성 OpenAPI에도 checked-in 명세와 같은 오류 응답 표면을 노출한다.

    FastAPI's default generated OpenAPI only documents route-local 200/422
    responses.  The platform runtime maps auth failures, request-size rejection,
    upstream timeouts, circuit-breaker/admission errors, and uncaught exceptions to
    the checked-in ``common_error`` contract.  Injecting those responses here keeps
    ``/docs`` from being weaker than ``specs/openapi.*.yaml``.

    Each error response is scoped to the codes that status actually returns (rather
    than the full enum) and its description glosses those codes, so a reader can map
    status -> code -> meaning directly in Scalar.
    """
    status_codes = _status_to_codes()
    catalog = _load_error_catalog()

    def with_gloss(status: str, existing: str | None) -> str:
        base = existing or STANDARD_ERROR_DESCRIPTIONS[status]
        gloss_codes = status_codes.get(status)
        return base + _error_code_gloss(gloss_codes, catalog) if gloss_codes else base

    for path_item in document.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})
            codes: list[str] = []
            if method.lower() in ("post", "patch", "put"):
                codes.extend(POST_STANDARD_ERROR_CODES)
            elif operation.get("security"):
                # Depends(auth)를 사용하는 FastAPI 라우트는 생성된 spec에 오퍼레이션별
                # "security" 필드를 만들지 않으므로, 이 분기는 라우트가 명시적으로
                # security=를 설정한 경우에만 실행된다(현재는 없음). admin auth를
                # 사용하는 GET 라우트는 라우터에서 직접 자체 401 응답을 선언한다.
                codes.append("401")
            for code in codes:
                if code == "401" and not operation.get("security"):
                    continue
                response = responses.setdefault(code, {})
                response["description"] = with_gloss(code, response.get("description"))
                content = response.setdefault("content", {}).setdefault("application/json", {})
                content["schema"] = _narrowed_error_schema(common_error_schema, code, status_codes)
            # 명시적으로 선언된 410 응답에 스키마를 주입한다 (예: retired 엔드포인트).
            if "410" in responses and "content" not in responses["410"]:
                content = responses["410"].setdefault("content", {}).setdefault("application/json", {})
                content["schema"] = _narrowed_error_schema(common_error_schema, "410", status_codes)

def find_project_root(start: Path | None = None) -> Path:
    """JSON 계약 스키마가 있는 저장소·설정 root를 찾는다."""
    candidates: list[Path] = []
    if start is not None:
        candidates.append(start)
    candidates.extend(Path(__file__).resolve().parents)
    candidates.append(Path.cwd())
    for candidate in candidates:
        root = candidate.resolve()
        if (root / "specs" / "schemas").exists() and (root / "VERSION").exists():
            return root
    raise RuntimeError("could not locate project root for OpenAPI contract schemas")


def load_contract_schema(schema_name: str, *, root: Path | None = None) -> dict[str, Any]:
    """JSON 스키마를 읽고 생성 OpenAPI용으로 외부 파일 참조를 인라인화한다.

    The checked-in contract schemas are the source of truth for runtime request and
    response validation.  FastAPI routes intentionally accept ``dict[str, Any]`` so
    the app can apply the platform's own contract validators and error mapping.
    Without this helper, generated OpenAPI falls back to a very loose ``object``
    schema.  Loading the same checked-in schemas here keeps ``/docs`` aligned with
    the runtime contract without changing request handling semantics.
    """
    project_root = find_project_root(root)
    schema_dir = project_root / "specs" / "schemas"
    schema = json.loads((schema_dir / schema_name).read_text(encoding="utf-8"))
    external_resolved = _resolve_external_refs(schema, schema_dir=schema_dir)
    return _resolve_internal_refs(external_resolved, root=external_resolved)


def _json_pointer(document: Any, pointer: str) -> Any:
    current = document
    for raw_part in pointer.lstrip("/").split("/") if pointer else []:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[part]
    return current


def _resolve_external_refs(value: Any, *, schema_dir: Path) -> Any:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            filename, _, pointer = ref.partition("#")
            external = json.loads((schema_dir / filename).read_text(encoding="utf-8"))
            resolved = copy.deepcopy(_json_pointer(external, pointer))
            return _resolve_external_refs(resolved, schema_dir=schema_dir)
        return {key: _resolve_external_refs(item, schema_dir=schema_dir) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_external_refs(item, schema_dir=schema_dir) for item in value]
    return value


def _resolve_internal_refs(value: Any, *, root: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#"):
            resolved = copy.deepcopy(_json_pointer(root, ref[1:]))
            return _resolve_internal_refs(resolved, root=root)
        return {key: _resolve_internal_refs(item, root=root) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_internal_refs(item, root=root) for item in value]
    return value


def narrow_chat_request_schema(
    schema: dict[str, Any], policies: "Sequence[dict[str, Any]] | None"
) -> dict[str, Any]:
    """chat 요청 스키마의 한도를 배포된 프로필 전체의 **상한**으로 맞춘다.

    한도는 프로필마다 다르다(예: max_output_tokens 13000 / 15000). 그런데 공개 문서는
    요청 시점의 활성 프로필을 알 수 없다 -- OpenAPI 문서는 첫 생성 후 캐시되고, 활성
    프로필은 admin sidecar가 들고 있는 런타임 상태이기 때문이다.

    그래서 특정 프로필 하나를 골라 싣지 않는다. 처음엔 기본 프로필을 썼는데, 활성
    프로필이 gemma4-e4b-it(15000)일 때 문서는 13000이라고 말했다. **스펙이 실제 API보다
    좁으면 스펙을 따른 클라이언트가 멀쩡한 요청을 못 보낸다.**

    대신 프로필 전체의 최댓값을 싣는다. 어떤 프로필이 활성이든 스펙이 좁아지지 않고,
    현재 프로필이 그보다 좁으면 런타임이 422와 error.param으로 알려준다. 정확한 현재
    값은 GET /v1/models가 준다.
    """
    policies = [p for p in (policies or []) if isinstance(p, dict)]
    properties = schema.get("properties")
    if not policies or not isinstance(properties, dict):
        return schema

    def ceiling(*path: str) -> int | None:
        values: list[int] = []
        for policy in policies:
            node: Any = policy
            for key in path:
                node = (node or {}).get(key) if isinstance(node, dict) else None
            if isinstance(node, int) and not isinstance(node, bool) and node > 0:
                values.append(node)
        return max(values) if values else None

    def set_bound(field: str, key: str, value: int | None) -> None:
        target = properties.get(field)
        if isinstance(target, dict) and value:
            target[key] = value

    max_output = ceiling("max_output_tokens")
    set_bound("max_tokens", "maximum", max_output)
    set_bound("max_completion_tokens", "maximum", max_output)
    set_bound("n", "maximum", ceiling("request_parameter_policy", "max_n"))
    set_bound("top_logprobs", "maximum", ceiling("request_parameter_policy", "top_logprobs", "max"))

    tools_ceiling = ceiling("request_parameter_policy", "tool_calling", "max_tools")
    any_tools = any(
        ((p.get("request_parameter_policy") or {}).get("tool_calling") or {}).get("enabled") is True
        for p in policies
    )
    if any_tools:
        set_bound("tools", "maxItems", tools_ceiling)
    # 도구 호출을 지원하는 프로필이 하나도 없을 때만 속성을 없앤다. 하나라도 지원하면
    # 남겨야 한다 -- 지원 프로필이 활성일 때 스펙이 그 요청을 거부하면 안 된다.
    else:
        for name in ("tools", "tool_choice", "parallel_tool_calls"):
            properties.pop(name, None)

    context_ceiling = ceiling("request_limits", "max_model_len")
    if context_ceiling:
        schema["description"] = (
            (schema.get("description", "") + " ").strip()
            + f" 여기 표시된 한도는 배포된 프로필 전체의 상한입니다(컨텍스트 최대 "
            f"{context_ceiling:,}토큰). 지금 활성화된 프로필은 더 좁을 수 있으니 "
            "`GET /v1/models`의 `request_parameters`로 확인하세요."
        ).strip()
    return schema


def install_contract_openapi(
    app: FastAPI,
    *,
    request_schemas: SchemaMap | None = None,
    response_schemas: SchemaMap | None = None,
    request_examples: ExamplesMap | None = None,
    schema_narrowers: dict[tuple[str, str], Any] | None = None,
    operation_details: dict[tuple[str, str], str] | None = None,
    root: Path | None = None,
) -> None:
    """FastAPI 생성 OpenAPI에 checked-in 계약 스키마를 반영한다."""
    request_schemas = request_schemas or {}
    response_schemas = response_schemas or {}
    request_examples = request_examples or {}
    schema_narrowers = schema_narrowers or {}
    operation_details = operation_details or {}
    original_openapi = app.openapi
    schema_cache: dict[str, dict[str, Any]] = {}

    def schema_for(schema_name: str) -> dict[str, Any]:
        if schema_name not in schema_cache:
            schema_cache[schema_name] = load_contract_schema(schema_name, root=root)
        return copy.deepcopy(schema_cache[schema_name])

    def contract_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        try:
            document = original_openapi()
            paths = document.setdefault("paths", {})
            # 설정에서 생성한 상세 설명을 해당 오퍼레이션에 붙인다. 엔드포인트가 하나뿐인
            # 태그(Chat/Models/Embeddings)는 이 내용이 태그 설명에 있었는데, 태그와
            # 오퍼레이션이 사실상 같은 것이라 나눌 이유가 없었고 무거운 쪽이 접혔다.
            for (method, path), detail in operation_details.items():
                operation = paths.get(path, {}).get(method.lower())
                if not isinstance(operation, dict) or not detail:
                    continue
                existing = (operation.get("description") or "").rstrip()
                operation["description"] = f"{existing}\n\n{detail}" if existing else detail
            for (method, path), schema_name in request_schemas.items():
                operation = paths.get(path, {}).get(method.lower())
                if not isinstance(operation, dict):
                    continue
                content = operation.setdefault("requestBody", {}).setdefault("content", {}).setdefault("application/json", {})
                request_schema = schema_for(schema_name)
                if narrower := schema_narrowers.get((method, path)):
                    request_schema = narrower(request_schema)
                content["schema"] = request_schema
                if examples := request_examples.get((method, path)):
                    content["examples"] = copy.deepcopy(examples)
                operation["requestBody"]["required"] = True
                operation.setdefault("x-contract-schema", schema_name)
            for (method, path), schema_name in response_schemas.items():
                operation = paths.get(path, {}).get(method.lower())
                if not isinstance(operation, dict):
                    continue
                responses = operation.setdefault("responses", {})
                response = responses.setdefault("200", {"description": "성공 응답"})
                if path == "/v1/chat/completions" and method.upper() == "POST":
                    response["description"] = "Main LLM runtime에서 반환한 OpenAI 호환 chat completion 응답. stream=true일 때는 text/event-stream SSE 응답을 반환한다."
                content = response.setdefault("content", {}).setdefault("application/json", {})
                content["schema"] = schema_for(schema_name)
                if path == "/v1/chat/completions" and method.upper() == "POST":
                    stream_content = response.setdefault("content", {}).setdefault("text/event-stream", {})
                    stream_content.setdefault(
                        "schema",
                        {
                            "type": "string",
                            "description": "OpenAI 호환 SSE 스트림. 스트리밍 전송 오류 시 Gateway는 SSE error 이벤트를 먼저 전송하고 data: [DONE]으로 종료합니다.",
                        },
                    )
                operation.setdefault("x-response-contract-schema", schema_name)
            _inject_standard_error_responses(document, schema_for("common_error.schema.json"))
            schemas = document.get("components", {}).get("schemas")
            if isinstance(schemas, dict):
                schemas.pop("HTTPValidationError", None)
                schemas.pop("ValidationError", None)
        except Exception:
            # original_openapi()가 FastAPI 기본 app.openapi()라서, 위 enrichment
            # 도중 실패해도 그 호출의 부작용으로 app.openapi_schema에 이미 가공 전
            # raw 스키마가 캐싱돼 있을 수 있다. 그 상태로 두면 다음 요청부터
            # `if app.openapi_schema:`가 그 raw 값을 계약 없이 영구 반환해버려서
            # (examples/실제 schema 없이 조용히 "정상"처럼 200을 계속 준다)
            # 에러가 처음 한 번만 나고 사라진다. 캐시를 지워서 원인이 고쳐질 때까지
            # 매 요청 실패로 계속 드러나게 한다.
            app.openapi_schema = None
            raise
        app.openapi_schema = document
        return document

    app.openapi = contract_openapi
