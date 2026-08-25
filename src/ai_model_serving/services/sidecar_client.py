from __future__ import annotations

from typing import Any

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

    VLLMClient와 같은 이유로 하나의 AsyncClient를 재사용한다: Gateway는 chat 요청마다
    이 client로 gate를 확인하기 때문에, 호출마다 client를 새로 만들면 추론 요청 하나당
    TCP 연결이 하나씩 새로 열리고 닫힌다. per-call timeout은 요청 단위로 지정한다.
    """

    def __init__(self, base_url: str, token: str = "") -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(headers=self._headers)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        what: str,
        timeout: float,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        request_error_statuses: tuple[int, ...] | range | None = None,
    ) -> Any:
        """sidecar를 호출하고 JSON body를 반환한다.

        ``request_error_statuses``에 해당하는 응답만 SidecarRequestError로 올린다
        (status code와 detail을 보존해야 호출자가 GPU 예산 거부 같은 것을 구분한다).
        나머지 실패는 SidecarUnavailableError다.
        """
        try:
            response = await self.client.request(
                method,
                f"{self._base}{path}",
                params=params,
                json=json,
                timeout=timeout,
            )
        except Exception as exc:
            raise SidecarUnavailableError(f"sidecar {what} failed: {exc}") from exc
        if request_error_statuses is not None and response.status_code in request_error_statuses:
            raise SidecarRequestError(response.status_code, _detail(response))
        if response.status_code >= 400:
            # 연결 자체는 됐으므로 상태 코드를 메시지에 남긴다. 이것이 없으면
            # sidecar가 500을 돌려준 경우와 sidecar에 닿지도 못한 경우가 호출자
            # 입장에서 완전히 같은 오류로 보인다.
            raise SidecarUnavailableError(
                f"sidecar {what} failed: HTTP {response.status_code}: {_detail(response)}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SidecarUnavailableError(f"sidecar {what} returned a non-JSON body") from exc

    async def get_status(self) -> dict[str, str]:
        """제어 가능한 모든 컨테이너의 ``{이름: 상태}`` 매핑을 반환한다."""
        body = await self._request("GET", "/containers/status", what="status", timeout=5.0)
        return body.get("containers", {})

    async def stop(self, container: str) -> list[str]:
        """컨테이너를 중지하고 실제로 중지한 컨테이너 목록을 반환한다."""
        body = await self._request(
            "POST", f"/containers/{container}/stop", what="stop", timeout=35.0
        )
        return body.get("stopped", [container])

    async def start(self, container: str, *, force: bool = False) -> dict:
        """컨테이너와 필요한 선행 컨테이너를 시작하고 sidecar 결과를 반환한다.

        A 409 GPU-budget rejection is surfaced as SidecarRequestError (carrying the
        eviction plan); connection/other failures remain SidecarUnavailableError.
        """
        body = await self._request(
            "POST",
            f"/containers/{container}/start",
            what="start",
            timeout=180.0,
            params={"force": "true"} if force else None,
            request_error_statuses=(409,),
        )
        return {
            "started": body.get("started", [container]),
            "evicted": body.get("evicted", []),
        }

    async def gpu_budget(self) -> dict:
        return await self._request("GET", "/gpu-budget", what="gpu-budget", timeout=5.0)

    async def main_stop(self) -> dict:
        return await self._request(
            "POST", "/main-model/stop", what="main-model stop", timeout=60.0
        )

    async def main_start(self, *, force: bool = False) -> dict:
        """main runtime을 시작한다. 예산 부족 409는 계획을 포함한 오류로 변환한다."""
        return await self._request(
            "POST",
            "/main-model/start",
            what="main-model start",
            timeout=180.0,
            params={"force": "true"} if force else None,
            request_error_statuses=(409,),
        )

    async def main_model(self, *, observed: bool = True) -> dict:
        """활성 main-model 상태를 반환한다.

        ``observed=False``는 sidecar가 Docker 관측(`observed_runtime`)을 건너뛰고
        control-plane ledger만 읽게 한다. ledger 필드(gate, active_profile,
        runtime_state, stats, last_operation)만 쓰는 호출자는 이 쪽을 써야 한다 --
        Docker inspect를 요청 경로에 직렬로 매달지 않기 위해서다.
        """
        # Sidecar는 관측 경로에서 Docker inspect(최대 5초)를 수행한다. 동일한
        # 5초 timeout을 Gateway에도 적용하면 응답 직전에 gateway가 먼저
        # 끊길 수 있으므로 transport 여유를 둔다. ledger 전용 조회는 파일 읽기라
        # 그 여유가 필요 없다.
        return await self._request(
            "GET",
            "/main-model",
            what="main-model status",
            timeout=10.0 if observed else 5.0,
            params=None if observed else {"observed": "false"},
        )

    async def main_model_profiles(self) -> list[dict]:
        body = await self._request(
            "GET", "/main-model/profiles", what="main-model profiles", timeout=5.0
        )
        return body.get("profiles", [])

    async def switch_main_model(
        self,
        profile: str,
        *,
        confirm_unverified: bool = False,
        request_id: str | None = None,
    ) -> dict:
        return await self._request(
            "POST",
            "/main-model/switch",
            what="main-model switch",
            timeout=10.0,
            json={
                "profile": profile,
                "confirm_unverified": confirm_unverified,
                **({"request_id": request_id} if request_id else {}),
            },
            request_error_statuses=range(400, 600),
        )

    async def main_model_operation(self, operation_id: str) -> dict:
        return await self._request(
            "GET",
            f"/main-model/operations/{operation_id}",
            what="operation status",
            timeout=5.0,
        )


def _detail(response: httpx.Response) -> object:
    try:
        body = response.json()
    except ValueError:
        return response.text
    return body.get("detail", response.text) if isinstance(body, dict) else response.text
