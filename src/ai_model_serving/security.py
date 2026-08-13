from __future__ import annotations

from hmac import compare_digest

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .settings import SecuritySettings


api_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="bearerAuth",
    description="Gateway API 또는 내부 Risk Adapter 호출에 사용하는 Bearer token입니다.",
)
admin_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="adminBearerAuth",
    description="/ready, /metrics 같은 운영자 endpoint에 사용하는 admin Bearer token입니다.",
)


def _require_token(
    credentials: HTTPAuthorizationCredentials | None,
    allowed_tokens: frozenset[str],
    *,
    label: str,
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail=f"Bearer {label} token is required.")
    token = credentials.credentials.strip()
    if not token or not allowed_tokens or not any(compare_digest(token, allowed) for allowed in allowed_tokens):
        raise HTTPException(status_code=401, detail=f"Bearer {label} token is invalid.")


def require_bearer_auth(settings: SecuritySettings):
    async def dependency(
        credentials: HTTPAuthorizationCredentials | None = Security(api_bearer_scheme),
    ) -> None:
        if not settings.api_key_required:
            return
        _require_token(credentials, settings.api_keys, label="API")

    return dependency


def require_admin_bearer_auth(settings: SecuritySettings):
    async def dependency(
        credentials: HTTPAuthorizationCredentials | None = Security(admin_bearer_scheme),
    ) -> None:
        if not settings.admin_api_key_required:
            return
        _require_token(credentials, settings.admin_api_keys, label="admin")

    return dependency
