from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Awaitable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse

from .docs_ui import scalar_html
from .errors import ServiceError, error_response, request_id_from_headers
from .logging_policy import safe_request_logging_middleware
from .metrics import Metrics
from .middleware import enforce_request_body_limit
from .security import require_admin_bearer_auth
from .settings import AppSettings

ValidationReasonResolver = Callable[[ServiceError], str | None]


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
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        code = "UNAUTHORIZED" if exc.status_code == 401 else "VALIDATION_ERROR"
        return error_response(code, str(exc.detail), False, exc.status_code, request_id_from_headers(request.headers))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response("VALIDATION_ERROR", str(exc), False, 422, request_id_from_headers(request.headers))

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception", exc_info=exc)
        return error_response("INTERNAL_ERROR", "Internal server error.", False, 500, request_id_from_headers(request.headers))


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
