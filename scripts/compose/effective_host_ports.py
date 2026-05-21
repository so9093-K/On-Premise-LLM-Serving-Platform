#!/usr/bin/env python3
"""Print host-published ports from `docker compose config` YAML on stdin."""
from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import Any

try:
    import yaml
except ModuleNotFoundError as exc:
    raise SystemExit("Missing dependency: PyYAML.") from exc


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


def main() -> int:
    data = yaml.safe_load(sys.stdin.read()) or {}
    if not isinstance(data, dict):
        raise SystemExit("effective compose config must be a YAML mapping")
    for service_name, host_port, bind in effective_host_ports(data):
        print(f"{service_name}|{host_port}|{bind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
