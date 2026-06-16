from __future__ import annotations

import httpx


class SidecarUnavailableError(Exception):
    pass


class SidecarClient:
    """HTTP client for the admin-sidecar container lifecycle API.

    The sidecar runs inside the compose network and is never exposed publicly.
    Timeouts are generous because container start includes health-wait loops.
    """

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")

    async def get_status(self) -> dict[str, str]:
        """Returns {container_name: status_string} for all controllable containers."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base}/containers/status")
                resp.raise_for_status()
                return resp.json().get("containers", {})
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar unreachable: {exc}") from exc

    async def stop(self, container: str) -> list[str]:
        """Stops container. Returns list of containers actually stopped."""
        try:
            async with httpx.AsyncClient(timeout=35.0) as client:
                resp = await client.post(f"{self._base}/containers/{container}/stop")
                resp.raise_for_status()
                return resp.json().get("stopped", [container])
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar stop failed: {exc}") from exc

    async def start(self, container: str) -> list[str]:
        """Starts container (and any prerequisites). Returns list of containers started."""
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(f"{self._base}/containers/{container}/start")
                resp.raise_for_status()
                return resp.json().get("started", [container])
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar start failed: {exc}") from exc
