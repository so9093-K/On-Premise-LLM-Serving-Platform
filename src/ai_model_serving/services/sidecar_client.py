from __future__ import annotations

import httpx


class SidecarUnavailableError(Exception):
    pass


class SidecarRequestError(Exception):
    def __init__(self, status_code: int, detail: object) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class SidecarClient:
    """HTTP client for the admin-sidecar container lifecycle API.

    The sidecar runs inside the compose network and is never exposed publicly.
    Timeouts are generous because container start includes health-wait loops.
    """

    def __init__(self, base_url: str, token: str = "") -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def get_status(self) -> dict[str, str]:
        """Returns {container_name: status_string} for all controllable containers."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base}/containers/status", headers=self._headers)
                resp.raise_for_status()
                return resp.json().get("containers", {})
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar unreachable: {exc}") from exc

    async def stop(self, container: str) -> list[str]:
        """Stops container. Returns list of containers actually stopped."""
        try:
            async with httpx.AsyncClient(timeout=35.0) as client:
                resp = await client.post(
                    f"{self._base}/containers/{container}/stop", headers=self._headers
                )
                resp.raise_for_status()
                return resp.json().get("stopped", [container])
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar stop failed: {exc}") from exc

    async def start(self, container: str) -> list[str]:
        """Starts container (and any prerequisites). Returns list of containers started."""
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{self._base}/containers/{container}/start", headers=self._headers
                )
                resp.raise_for_status()
                return resp.json().get("started", [container])
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar start failed: {exc}") from exc

    async def main_model(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base}/main-model", headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar main-model status failed: {exc}") from exc

    async def main_model_profiles(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self._base}/main-model/profiles", headers=self._headers
                )
                resp.raise_for_status()
                return resp.json().get("profiles", [])
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar main-model profiles failed: {exc}") from exc

    async def switch_main_model(
        self,
        profile: str,
        *,
        confirm_unverified: bool = False,
        request_id: str | None = None,
    ) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base}/main-model/switch",
                    headers=self._headers,
                    json={
                        "profile": profile,
                        "confirm_unverified": confirm_unverified,
                        **({"request_id": request_id} if request_id else {}),
                    },
                )
                if resp.status_code >= 400:
                    try:
                        detail = resp.json().get("detail", resp.text)
                    except ValueError:
                        detail = resp.text
                    raise SidecarRequestError(resp.status_code, detail)
                return resp.json()
        except (SidecarUnavailableError, SidecarRequestError):
            raise
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar main-model switch failed: {exc}") from exc

    async def rollback_main_model(self, *, request_id: str | None = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base}/main-model/rollback",
                    headers=self._headers,
                    json={"request_id": request_id} if request_id else {},
                )
                if resp.status_code >= 400:
                    try:
                        detail = resp.json().get("detail", resp.text)
                    except ValueError:
                        detail = resp.text
                    raise SidecarRequestError(resp.status_code, detail)
                return resp.json()
        except (SidecarUnavailableError, SidecarRequestError):
            raise
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar main-model rollback failed: {exc}") from exc

    async def main_model_operation(self, operation_id: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self._base}/main-model/operations/{operation_id}",
                    headers=self._headers,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar operation status failed: {exc}") from exc
