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
    """admin-sidecar 컨테이너 lifecycle API용 HTTP client다.

    The sidecar runs inside the compose network and is never exposed publicly.
    Timeouts are generous because container start includes health-wait loops.
    """

    def __init__(self, base_url: str, token: str = "") -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def get_status(self) -> dict[str, str]:
        """제어 가능한 모든 컨테이너의 ``{이름: 상태}`` 매핑을 반환한다."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base}/containers/status", headers=self._headers)
                resp.raise_for_status()
                return resp.json().get("containers", {})
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar unreachable: {exc}") from exc

    async def stop(self, container: str) -> list[str]:
        """컨테이너를 중지하고 실제로 중지한 컨테이너 목록을 반환한다."""
        try:
            async with httpx.AsyncClient(timeout=35.0) as client:
                resp = await client.post(
                    f"{self._base}/containers/{container}/stop", headers=self._headers
                )
                resp.raise_for_status()
                return resp.json().get("stopped", [container])
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar stop failed: {exc}") from exc

    async def start(self, container: str, *, force: bool = False) -> dict:
        """컨테이너와 필요한 선행 컨테이너를 시작하고 sidecar 결과를 반환한다.

        A 409 GPU-budget rejection is surfaced as SidecarRequestError (carrying the
        eviction plan); connection/other failures remain SidecarUnavailableError.
        """
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{self._base}/containers/{container}/start",
                    headers=self._headers,
                    params={"force": "true"} if force else None,
                )
                if resp.status_code == 409:
                    try:
                        detail = resp.json().get("detail", resp.text)
                    except ValueError:
                        detail = resp.text
                    raise SidecarRequestError(409, detail)
                resp.raise_for_status()
                body = resp.json()
                return {
                    "started": body.get("started", [container]),
                    "evicted": body.get("evicted", []),
                }
        except (SidecarUnavailableError, SidecarRequestError):
            raise
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar start failed: {exc}") from exc

    async def gpu_budget(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base}/gpu-budget", headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar gpu-budget failed: {exc}") from exc

    async def main_stop(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{self._base}/main-model/stop", headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar main-model stop failed: {exc}") from exc

    async def main_start(self, *, force: bool = False) -> dict:
        """main runtime을 시작한다. 예산 부족 409는 계획을 포함한 오류로 변환한다."""
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{self._base}/main-model/start",
                    headers=self._headers,
                    params={"force": "true"} if force else None,
                )
                if resp.status_code == 409:
                    try:
                        detail = resp.json().get("detail", resp.text)
                    except ValueError:
                        detail = resp.text
                    raise SidecarRequestError(409, detail)
                resp.raise_for_status()
                return resp.json()
        except (SidecarUnavailableError, SidecarRequestError):
            raise
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar main-model start failed: {exc}") from exc

    async def main_model(self) -> dict:
        try:
            # Sidecar는 이 조회에서 Docker inspect(최대 5초)를 수행한다. 동일한
            # 5초 timeout을 Gateway에도 적용하면 응답 직전에 gateway가 먼저
            # 끊길 수 있으므로 transport 여유를 둔다.
            async with httpx.AsyncClient(timeout=10.0) as client:
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
