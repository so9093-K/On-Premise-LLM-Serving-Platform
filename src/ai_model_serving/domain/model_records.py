from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


def resolve_catalog_max_output_tokens(catalog_entry: dict[str, Any]) -> int | None:
    """catalog runtime에 선언된 고정 max_output_tokens를 반환한다.

    일반 serving runtime의 정책값은 ``model_serving.yaml``이 소유한다. 이 값은
    risk-prompt처럼 catalog identity와 함께 고정된 출력 계약만 위한 fallback이다.
    """
    runtime = catalog_entry.get("runtime", {})
    value = runtime.get("max_output_tokens")
    return int(value) if value is not None else None


@dataclass(frozen=True)
class PublicModel:
    """Gateway가 노출하는 OpenAI 호환 model listing projection."""

    id: str
    backend: str
    capabilities: tuple[str, ...]
    request_parameters: dict[str, dict[str, Any]]
    fixed_parameters: dict[str, Any]
    input_modalities: tuple[str, ...] = ()
    object: str = "model"

    def as_response_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": self.id,
            "object": self.object,
            "backend": self.backend,
            "capabilities": list(self.capabilities),
            "request_parameters": dict(self.request_parameters),
        }
        # input_modalities는 클라이언트가 보낼 수 있는 입력 콘텐츠 타입이다. 정적
        # listing은 catalog 기본값을 담고 있으며, gateway가 요청 시점에 *active*
        # main-model 프로필의 modality를 덮어써서, 클라이언트가 신뢰할 수 있는
        # 유일한 소스로 유지되도록 한다 -- chat validator가 강제하는 것과 동일한
        # 집합이다.
        if self.input_modalities:
            item["input_modalities"] = list(self.input_modalities)
        if self.fixed_parameters:
            item["fixed_parameters"] = dict(self.fixed_parameters)
        return item


@dataclass(frozen=True)
class ModelRecord:
    """logical model 하나에 대한 정규화된 domain view.

    catalog가 source of truth이지만 호출자는 raw YAML dictionary 대신 이 object를 사용한다. ``serving_key``는 runtime deployment stanza가 생기기 전에도 문서 렌더링 같은 catalog-only workflow가 동작해야 하므로 optional이다.
    """

    logical_id: str
    role: str
    upstream_model_id: str
    served_model_name: str
    backend: str
    capabilities: tuple[str, ...]
    request_parameters: dict[str, dict[str, Any]]
    fixed_parameters: dict[str, Any]
    public_enabled: bool
    serving_key: str | None = None
    port: int | None = None
    endpoint_path: str | None = None
    max_model_len: int | None = None
    max_output_tokens: int | None = None
    embedding_dimensions: tuple[int, ...] = ()
    input_modalities: tuple[str, ...] = ()
    lifecycle_state: str = "active"
    exposure: str = "public"

    def public_model(self) -> PublicModel | None:
        if not self.public_enabled:
            return None
        return PublicModel(
            id=self.logical_id,
            backend=self.backend,
            capabilities=self.capabilities,
            request_parameters=self.request_parameters,
            fixed_parameters=self.fixed_parameters,
            input_modalities=self.input_modalities,
        )


@dataclass(frozen=True)
class RuntimeService:
    """설정된 vLLM runtime service 하나에 대한 정규화된 view."""

    service_key: str
    logical_id: str | None
    role: str
    upstream_model_id: str
    served_model_name: str
    port: int
    endpoint_path: str | None
    endpoint_url: str
    backend: str
    config: dict[str, Any]

    @property
    def compose_service_name(self) -> str:
        """설정된 endpoint URL에서 Compose DNS service name을 반환한다."""
        parsed = urlparse(self.endpoint_url)
        return parsed.hostname or ""


@dataclass(frozen=True)
class RegistryIssue:
    code: str
    message: str
