"""configs/services.yaml의 publish 주소를 host에서 접속 가능한 URL로 바꾼다.

runtime validation과 benchmark가 각자 이 규칙을 다시 쓰면 두 도구가 서로 다른
Gateway를 재게 된다. services.yaml이 주소를 소유하고, 해석은 여기 한 곳만 한다.
"""
from __future__ import annotations

import os
from typing import Any

# Compose의 wildcard bind는 listen 주소이지 접속 대상이 아니다.
_WILDCARD_BINDS = {"0.0.0.0", "::", "[::]"}


def _published_host(service: dict[str, Any]) -> str:
    bind = os.getenv(str(service.get("host_env_bind", "")), "").strip()
    return bind if bind and bind not in _WILDCARD_BINDS else "localhost"


def _published_port(service: dict[str, Any]) -> int:
    """운영자가 port를 바꿔 띄웠으면 그 port로 접속해야 한다."""
    override = os.getenv(str(service.get("host_env_port", "")), "").strip()
    if override:
        try:
            return int(override)
        except ValueError as exc:
            raise ValueError(f"{service.get('host_env_port')}={override!r} is not a port number") from exc
    try:
        return int(service["default_host_port"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("service registry entry requires default_host_port") from exc


def published_base_url(services: dict[str, Any], service_id: str, suffix: str = "") -> str:
    try:
        service = services[service_id]
    except KeyError as exc:
        raise ValueError(f"configs/services.yaml is missing {service_id}") from exc
    return service_base_url(service, suffix)


def service_base_url(service: dict[str, Any], suffix: str = "") -> str:
    return f"http://{_published_host(service)}:{_published_port(service)}{suffix}"
