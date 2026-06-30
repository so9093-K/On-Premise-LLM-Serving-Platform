from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


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
        # input_modalities is the input content types a client may send. The static
        # listing carries the catalog default; the gateway overlays the *active*
        # main-model profile's modalities at request time so this stays the single
        # source clients can trust -- the same set the chat validator enforces.
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
    output_modalities: tuple[str, ...] = ()
    lifecycle_state: str = "active"
    exposure: str = "public"
    owner: str = "platform"

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
