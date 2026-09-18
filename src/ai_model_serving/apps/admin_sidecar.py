"""Legacy Runtime Controller application-module compatibility.

새 runtime entrypoint와 source consumer는 ``ai_model_serving.apps.runtime_controller``를 사용한다.
Compose service ID ``admin-sidecar``와 이 module import는 compatibility window 동안 유지된다.
"""

from .runtime_controller import (
    RuntimeControllerConfig,
    app,
    load_runtime_controller_config,
)

SidecarConfig = RuntimeControllerConfig
load_sidecar_config = load_runtime_controller_config

__all__ = [
    "RuntimeControllerConfig",
    "load_runtime_controller_config",
    "SidecarConfig",
    "load_sidecar_config",
    "app",
]
