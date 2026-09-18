"""Legacy Runtime Controller client import compatibility.

새 application code는 runtime_controller_client의 canonical 이름을 사용한다.
이 module과 Sidecar* 이름은 compatibility window 동안 import alias로만 유지한다.
"""

from .runtime_controller_client import (
    RuntimeControllerClient,
    RuntimeControllerRequestError,
    RuntimeControllerUnavailableError,
)

SidecarClient = RuntimeControllerClient
SidecarRequestError = RuntimeControllerRequestError
SidecarUnavailableError = RuntimeControllerUnavailableError

__all__ = [
    "SidecarClient",
    "SidecarRequestError",
    "SidecarUnavailableError",
]
