from __future__ import annotations

import warnings
from typing import Any

from .env import as_bool, env, is_default_secret
from .types import SecuritySettings

LOCAL_ENVIRONMENTS = {"local", "test", "development"}


def is_non_local_env(app_env: str) -> bool:
    return app_env.lower() not in LOCAL_ENVIRONMENTS


def build_security_settings(
    *,
    app_env: str,
    security_cfg: dict[str, Any],
    internal_service_token_required: bool,
) -> SecuritySettings:
    non_local_env = is_non_local_env(app_env)
    api_key_required = as_bool(
        env("API_KEY_REQUIRED", str(security_cfg.get("api_key_required", True))),
        True,
    )
    if non_local_env and not api_key_required:
        warnings.warn(
            f"API_KEY_REQUIRED=false: Gateway API endpoints are unauthenticated (APP_ENV={app_env}). "
            "Set API_KEY_REQUIRED=true for any public-facing deployment.",
            UserWarning,
            stacklevel=2,
        )

    api_keys = frozenset(key.strip() for key in env("API_KEYS", "change-me").split(",") if key.strip())
    if api_key_required and non_local_env:
        if not api_keys or any(is_default_secret(key) for key in api_keys):
            raise RuntimeError("API_KEYS must be set to non-default values when APP_ENV is not local/test/development. Run `make init-env-compose` to generate safe local secrets.")

    auth_mode = env("AUTH_MODE", str(security_cfg.get("auth_mode", "custom"))).strip() or "custom"
    internal_service_token = env("INTERNAL_SERVICE_TOKEN", str(security_cfg.get("internal_service_token", "change-me-internal")))
    internal_service_auth_required = as_bool(
        env("INTERNAL_SERVICE_AUTH_REQUIRED", str(security_cfg.get("internal_service_auth_required", True))),
        True,
    )
    if (
        internal_service_token_required
        and internal_service_auth_required
        and non_local_env
        and is_default_secret(internal_service_token)
    ):
        raise RuntimeError("INTERNAL_SERVICE_TOKEN must be set to a non-default value when INTERNAL_SERVICE_AUTH_REQUIRED=true outside local/test/development. Run `make init-env-compose` to generate one.")

    admin_api_key_required = as_bool(
        env("ADMIN_API_KEY_REQUIRED", str(security_cfg.get("admin_api_key_required", False))),
        False,
    )
    admin_keys_env = env("ADMIN_API_KEYS", env("ADMIN_API_KEY", ""))
    admin_api_keys = frozenset(key.strip() for key in admin_keys_env.split(",") if key.strip())
    if admin_api_key_required and non_local_env:
        if not admin_api_keys or any(is_default_secret(key) for key in admin_api_keys):
            raise RuntimeError("ADMIN_API_KEY or ADMIN_API_KEYS must be set to non-default values when ADMIN_API_KEY_REQUIRED=true outside local/test/development. Run `make init-env-compose` to generate one.")

    admin_endpoints_internal_only = as_bool(
        env("ADMIN_ENDPOINTS_INTERNAL_ONLY", str(security_cfg.get("admin_endpoints_internal_only", False))),
        False,
    )
    strict_admin = as_bool(env("STRICT_ADMIN_ENDPOINT_SECURITY", "false"), False)
    if non_local_env and not admin_api_key_required and not admin_endpoints_internal_only:
        message = (
            f"ADMIN_API_KEY_REQUIRED=false and ADMIN_ENDPOINTS_INTERNAL_ONLY=false (APP_ENV={app_env}): "
            "/ready and /metrics are unauthenticated. Ensure these endpoints are not publicly reachable."
        )
        if strict_admin:
            raise RuntimeError(message)
        warnings.warn(message, UserWarning, stacklevel=2)

    return SecuritySettings(
        api_key_required=api_key_required,
        api_keys=api_keys,
        internal_service_token=internal_service_token,
        internal_service_auth_required=internal_service_auth_required,
        auth_mode=auth_mode,
        admin_api_key_required=admin_api_key_required,
        admin_api_keys=admin_api_keys,
        admin_endpoints_internal_only=admin_endpoints_internal_only,
    )
