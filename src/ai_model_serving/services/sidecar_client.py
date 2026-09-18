"""Compatibility exports for the renamed Runtime Controller client.

New code should import from :mod:`ai_model_serving.services.runtime_controller_client`.
The legacy module and class names remain aliases during the identifier migration.
"""

from __future__ import annotations

from .runtime_controller_client import (
    RuntimeControllerClient,
    RuntimeControllerRequestError,
    RuntimeControllerUnavailableError,
)

SidecarClient = RuntimeControllerClient
SidecarRequestError = RuntimeControllerRequestError
SidecarUnavailableError = RuntimeControllerUnavailableError

__all__ = [
    "RuntimeControllerClient",
    "RuntimeControllerRequestError",
    "RuntimeControllerUnavailableError",
    "SidecarClient",
    "SidecarRequestError",
    "SidecarUnavailableError",
]
