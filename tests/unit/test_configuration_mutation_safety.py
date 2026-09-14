from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from ai_model_serving.apps.gateway import create_gateway_app
from ai_model_serving.configuration_mutation import (
    ConfigurationHistoryStore,
    ConfigurationMutationEngine,
    ConfigurationValidationError,
)
from ai_model_serving.configuration_plane import configuration_schema_items
from ai_model_serving.metrics import Metrics
from ai_model_serving.operator_configuration import (
    ConfigurationValueResolver,
    OperatorConfigurationStore,
    operator_metadata_by_key,
    repository_operator_defaults,
    runtime_snapshot_from_resolver,
)
from ai_model_serving.runtime_configuration import RuntimeConfigurationProvider
from ai_model_serving.services.gateway_service import GatewayService
from tests.unit.gateway.helpers import FakeGatewayClients, settings


def _mutation_engine(tmp_path: Path) -> tuple[OperatorConfigurationStore, ConfigurationMutationEngine]:
    schema_items = configuration_schema_items()
    store = OperatorConfigurationStore(
        tmp_path / "config" / "operator-overrides.yaml",
        operator_metadata_by_key(schema_items),
    )
    resolver = ConfigurationValueResolver(
        repository_defaults=repository_operator_defaults(schema_items),
        operator_state=store.read(),
    )
    runtime = RuntimeConfigurationProvider(
        runtime_snapshot_from_resolver(resolver, schema_items)
    )
    engine = ConfigurationMutationEngine(
        schema_items=schema_items,
        store=store,
        resolver=resolver,
        runtime_configuration=runtime,
        history=ConfigurationHistoryStore(tmp_path / "config" / "history"),
    )
    return store, engine


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_plan_rejects_non_finite_numeric_values_before_persistence(
    tmp_path: Path,
    value: float,
) -> None:
    store, engine = _mutation_engine(tmp_path)

    with pytest.raises(ConfigurationValidationError, match="finite number"):
        engine.plan(
            base_revision=0,
            changes=[
                {
                    "key": "streaming.max_duration_seconds",
                    "op": "set",
                    "value": value,
                }
            ],
        )

    assert store.read().revision == 0
    assert store.read().overrides == {}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_runtime_snapshot_rejects_non_finite_stream_duration(value: float) -> None:
    provider = RuntimeConfigurationProvider.from_settings(settings())

    with pytest.raises(ValueError, match="finite number"):
        provider.update(streaming_max_duration_seconds=value)

    assert provider.snapshot().revision == 0


def test_in_flight_stream_pins_one_runtime_configuration_snapshot() -> None:
    app_settings = settings()
    provider = RuntimeConfigurationProvider.from_settings(app_settings)
    provider.update(streaming_max_chunks=4)
    service = GatewayService(
        provider.settings_view(app_settings),
        FakeGatewayClients(),
        Metrics("stream_snapshot_test"),
    )

    async def upstream():
        yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
        provider.update(streaming_max_chunks=1)
        yield b'data: {"choices":[{"delta":{"content":"second"}}]}\n\n'
        yield b'data: [DONE]\n\n'

    async def collect() -> bytes:
        parts = [
            chunk
            async for chunk in service._relay_chat_stream(
                upstream(),
                target="local-main",
                start=time.monotonic(),
            )
        ]
        return b"".join(parts)

    body = asyncio.run(collect())

    assert b"STREAM_LIMIT_EXCEEDED" not in body
    assert b"second" in body
    assert provider.snapshot().streaming_max_chunks == 1


def test_apply_openapi_declares_required_if_match_header() -> None:
    app = create_gateway_app(settings(), FakeGatewayClients())
    operation = app.openapi()["paths"]["/admin/config"]["patch"]
    parameters = operation.get("parameters", [])
    if_match = next(parameter for parameter in parameters if parameter["name"] == "If-Match")

    assert if_match["in"] == "header"
    assert if_match["required"] is True
    assert if_match["schema"]["pattern"] == '^"config-[0-9]+"$'
