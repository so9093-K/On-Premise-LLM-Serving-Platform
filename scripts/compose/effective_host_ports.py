"""Docker Compose 설정에서 host-published port를 추출한다."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _short_port(value: str) -> tuple[str, str] | None:
    parts = value.rsplit(":", 2)
    if len(parts) == 3:
        return parts[0], parts[1]
    if len(parts) == 2:
        return "0.0.0.0", parts[0]
    return None


def effective_host_ports(document: dict[str, Any]) -> Iterable[tuple[str, str, str]]:
    for service_name, service in (document.get("services") or {}).items():
        if not isinstance(service, dict):
            continue
        for port in service.get("ports") or []:
            parsed: tuple[str, str] | None
            if isinstance(port, str):
                parsed = _short_port(port)
            elif isinstance(port, dict):
                parsed = (
                    str(port.get("host_ip") or "0.0.0.0"),
                    str(port.get("published") or ""),
                )
            else:
                parsed = None
            if parsed and parsed[1]:
                yield service_name, parsed[1], parsed[0]
