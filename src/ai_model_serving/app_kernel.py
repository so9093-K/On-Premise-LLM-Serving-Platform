from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Awaitable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .docs_ui import scalar_html
from .errors import (
    ServiceError,
    default_code_for_status,
    error_response,
    exception_debug,
    request_id_from_headers,
    service_error_debug,
)
from .logging_policy import safe_request_logging_middleware
from .metrics import Metrics
from .middleware import enforce_request_body_limit
from .security import require_admin_bearer_auth
from .settings import AppSettings

ValidationReasonResolver = Callable[[ServiceError], str | None]

# Pydantic은 모든 body 필드 위치 앞에 request part("body")를 붙이는데, 이는
# API 클라이언트 입장에서는 노이즈다. 이를 제거하여 param/message가 실제 필드
# 이름을 그대로 나타내도록 한다.
_LOC_PART_MARKERS = {"body", "query", "path", "header", "cookie"}


def _field_path(loc: Any) -> str | None:
    parts = [str(part) for part in loc]
    if parts and parts[0] in _LOC_PART_MARKERS:
        parts = parts[1:]
    return ".".join(parts) if parts else None


@asynccontextmanager
async def managed_lifespan(*resources: Any) -> AsyncIterator[None]:
    """Close app-owned resources that expose an async ``close`` method.

    Gateway and Risk Adapter have the same lifecycle shape: construct clients
    during app creation and close them on shutdown.  Keeping that lifecycle in
    one place makes future resources such as registries, probes, or exporters
    easier to attach without duplicating shutdown code in every app module.
    """
    try:
        yield
    finally:
        for resource in resources:
            close = getattr(resource, "close", None)
            if close is not None:
                await close()


def create_service_app(
    *,
    title: str,
    version: str,
    description: str,
    settings: AppSettings,
    tags_metadata: list[dict[str, Any]],
    lifespan_resources: tuple[Any, ...] = (),
) -> FastAPI:
    """Create a FastAPI app with platform-wide documentation defaults."""
    return FastAPI(
        title=title,
        version=version,
        description=description,
        lifespan=lambda app: managed_lifespan(*lifespan_resources),
        docs_url=None,
        redoc_url=settings.documentation.redoc_url if settings.documentation.enabled else None,
        openapi_url=settings.documentation.openapi_url if settings.documentation.enabled else None,
        openapi_tags=tags_metadata,
        contact={"name": "AI Model Serving Platform 운영"},
    )


def admin_dependencies(settings: AppSettings) -> list[Depends]:
    """Return admin auth dependencies without changing auth/non-auth mode semantics."""
    return [Depends(require_admin_bearer_auth(settings.security))] if settings.security.admin_api_key_required else []


def install_common_middleware(
    app: FastAPI,
    *,
    settings: AppSettings,
    metrics: Metrics,
    logger: Any,
) -> None:
    """Install request-size guard, HTTP metrics, and safe access logging."""

    @app.middleware("http")
    async def request_size_guard(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
        return await enforce_request_body_limit(
            request,
            call_next,
            max_body_bytes=settings.max_request_body_bytes,
        )

    app.middleware("http")(metrics.http_middleware)

    @app.middleware("http")
    async def safe_access_log(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
        return await safe_request_logging_middleware(
            request,
            call_next,
            logger=logger,
            service=metrics.service,
        )


def install_cors_middleware(app: FastAPI, *, settings: AppSettings) -> None:
    """Allow a browser-based client (e.g. a chat webui) on another origin to call this API.

    `CORS_ALLOWED_ORIGINS` 기본값은 전체 허용("*")이다 — 이 프로젝트의 기본 auth
    profile(local_open)이 API 키 인증까지 기본으로 끄고 "네트워크 경계가 접근 제어를
    소유한다"는 전제라, CORS만 기본으로 닫아두는 게 오히려 기조에 안 맞는다. vLLM
    자체도 기본이 이렇다(`allow_origins=["*"]`). 인증은 쿠키가 아니라 Bearer 토큰이라
    `allow_credentials`는 안 쓴다(그 조합은 CORS 스펙상 `allow_origins=["*"]`와 같이
    못 쓰기도 하고, 이 프로젝트 인증 방식엔 애초에 불필요하다). `CORS_ALLOWED_ORIGINS`를
    빈 값으로 두면 미들웨어 자체를 안 붙여서 cross-origin을 전부 막을 수 있다(더 엄격한
    프로필용). 반드시 `install_common_middleware` 이후에 호출해야 가장 바깥쪽에 위치해,
    preflight(OPTIONS)가 요청 크기 가드/메트릭/접근 로그보다 먼저 처리된다.
    """
    if not settings.cors.allowed_origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors.allowed_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def install_exception_handlers(
    app: FastAPI,
    *,
    metrics: Metrics,
    logger: Any,
    validation_reason: ValidationReasonResolver | None = None,
) -> None:
    """Install platform-standard JSON error handlers.

    Services can provide a small validation reason resolver to keep their
    service-specific metric labels without copying the handler implementation.
    """

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
        if exc.code == "VALIDATION_ERROR":
            reason = validation_reason(exc) if validation_reason is not None else "request"
            metrics.record_validation_rejection(reason or "request")
        return error_response(
            exc.code,
            exc.message,
            exc.retryable,
            exc.status_code,
            request_id_from_headers(request.headers),
            exc.param,
            service_error_debug(exc),
            exc.retry_after_seconds,
        )

    # Starlette 기본 클래스에 등록하여, 매칭되지 않은 route에서 발생하는 404/405도
    # (router가 FastAPI의 서브클래스가 아니라 기본 HTTPException으로 raise한다)
    # Starlette의 그냥 {"detail": ...} 대신 code와 request_id가 포함된 platform
    # error envelope를 받도록 한다. FastAPI의 HTTPException이 이를 서브클래싱하므로,
    # route 내부에서 발생하는 raise는 변경 없이 그대로 여기를 거쳐 흐른다.
    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = default_code_for_status(exc.status_code)
        return error_response(code, str(exc.detail), False, exc.status_code, request_id_from_headers(request.headers))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        # JSON 파싱에 실패한 body는 단일 json_invalid 에러로 나타나며, 그 loc는
        # 필드가 아니라 syntax error의 byte offset이다. 이 offset을 필드 경로로
        # 취급하면 의미 없는 param(예: "22")과 알아보기 힘든 "22: JSON decode error"
        # 메시지가 나오므로, 명시적으로 처리한다: parse 실패 사유를 이름으로 남기고
        # param은 비워둔다(식별 가능한 필드가 없으므로).
        if len(errors) == 1 and errors[0].get("type") == "json_invalid":
            reason = (errors[0].get("ctx") or {}).get("error") or errors[0].get("msg", "invalid JSON")
            return error_response(
                "VALIDATION_ERROR",
                f"Request body is not valid JSON: {reason}.",
                False,
                422,
                request_id_from_headers(request.headers),
            )
        parts = [
            f"{_field_path(e.get('loc', ())) or 'request'}: {e.get('msg', '')}"
            for e in errors
        ]
        message = "; ".join(parts) if parts else str(exc)
        # 클라이언트가 합쳐진 메시지를 파싱하지 않고도 에러 원인을 기준으로 분기할
        # 수 있도록, 첫 번째로 문제가 된 필드를 param으로 노출한다(contract
        # validator가 ServiceError에 설정하는 것과 동일한 field-pointer다).
        param = _field_path(errors[0].get("loc", ())) if errors else None
        return error_response("VALIDATION_ERROR", message, False, 422, request_id_from_headers(request.headers), param)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception", exc_info=exc)
        return error_response(
            "INTERNAL_ERROR",
            "Internal server error.",
            False,
            500,
            request_id_from_headers(request.headers),
            debug=exception_debug(exc),
        )


def register_scalar_docs(app: FastAPI, *, settings: AppSettings, title: str) -> None:
    """Register the Scalar documentation endpoint when docs are enabled."""
    if not settings.documentation.enabled:
        return

    docs_url = settings.documentation.docs_url
    openapi_url = settings.documentation.openapi_url

    @app.get(docs_url, include_in_schema=False)
    async def scalar_docs() -> HTMLResponse:
        return HTMLResponse(scalar_html(openapi_url, title))


def register_health(app: FastAPI, *, service: str, operation_id: str | None = None) -> None:
    """Register the standard liveness endpoint."""
    kwargs: dict[str, Any] = {"tags": ["Operations"], "summary": "Liveness 확인"}
    if operation_id is not None:
        kwargs["operation_id"] = operation_id

    @app.get("/health", **kwargs)
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": service}


def readiness_response(body: dict[str, Any]) -> JSONResponse:
    """Return the platform readiness body using 200/503 semantics."""
    status_code = 200 if body.get("status") == "ready" else 503
    return JSONResponse(body, status_code=status_code)
