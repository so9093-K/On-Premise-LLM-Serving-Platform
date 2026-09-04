from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...configuration_plane import configuration_schema, effective_configuration
from ..endpoint_spec import GATEWAY_ENDPOINTS

_GW = {(spec.method, spec.path): spec for spec in GATEWAY_ENDPOINTS}


def build_router(admin_dependencies: list, settings: Any) -> APIRouter:
    router = APIRouter()
    schema_spec = _GW[("GET", "/admin/config/schema")]
    effective_spec = _GW[("GET", "/admin/config/effective")]

    @router.get(
        "/admin/config/schema", dependencies=admin_dependencies,
        tags=[schema_spec.tag], summary=schema_spec.summary,
        description=schema_spec.description, operation_id=schema_spec.operation_id,
    )
    async def config_schema() -> dict[str, Any]:
        return configuration_schema()

    @router.get(
        "/admin/config/effective", dependencies=admin_dependencies,
        tags=[effective_spec.tag], summary=effective_spec.summary,
        description=effective_spec.description, operation_id=effective_spec.operation_id,
    )
    async def config_effective() -> dict[str, Any]:
        return effective_configuration(settings)

    return router
