from __future__ import annotations

from pathlib import Path

import pytest

from ai_model_serving.configuration_contract import ConfigurationValidationError
from ai_model_serving.configuration_history import ConfigurationHistoryStore
from ai_model_serving.configuration_mutation import ConfigurationMutationEngine
from ai_model_serving.configuration_plane import configuration_schema_items
from ai_model_serving.operator_configuration import (
    ConfigurationValueResolver,
    OperatorConfigurationStore,
    operator_metadata_by_key,
    repository_operator_defaults,
    runtime_snapshot_from_resolver,
)
from ai_model_serving.runtime_configuration import RuntimeConfigurationProvider


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
