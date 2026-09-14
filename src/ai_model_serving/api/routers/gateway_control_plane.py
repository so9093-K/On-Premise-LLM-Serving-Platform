from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ...control_plane_bootstrap import control_plane_bootstrap_document
from ...configuration_mutation import ConfigurationMutationEngine
from ...operator_configuration import ConfigurationValueResolver
from ..endpoint_spec import GATEWAY_ENDPOINTS

_GW = {(spec.method, spec.path): spec for spec in GATEWAY_ENDPOINTS}


def build_router(
    settings: Any,
    resolver: ConfigurationValueResolver,
    mutation: ConfigurationMutationEngine,
) -> APIRouter:
    router = APIRouter()
    spec = _GW[("GET", "/admin/control-plane/bootstrap")]

    @router.get(
        "/admin/control-plane/bootstrap",
        tags=[spec.tag],
        summary=spec.summary,
        description=spec.description,
        operation_id=spec.operation_id,
    )
    async def control_plane_bootstrap(request: Request) -> dict[str, Any]:
        status = mutation.status()
        return control_plane_bootstrap_document(
            settings,
            configuration_revision=resolver.revision,
            configuration_write_available=status.get("available") is True,
            request_hostname=request.url.hostname,
        )

    return router
