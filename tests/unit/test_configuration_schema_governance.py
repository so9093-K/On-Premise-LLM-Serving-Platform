from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.validation.governance import configuration_plane as governance
from scripts.validation.governance.common import read_json as real_read_json
from scripts.validation.governance.common import read_yaml as real_read_yaml


def test_configuration_schema_v2_governance_accepts_repository_contracts() -> None:
    governance.validate_configuration_schema()


def test_configuration_schema_v2_governance_rejects_retrieval_contract_drift(monkeypatch) -> None:
    def read_json(path: str):
        document = deepcopy(real_read_json(path))
        if path.endswith("retrieval_score_request.schema.json"):
            document["properties"]["documents"]["maxItems"] = 31
        return document

    monkeypatch.setattr(governance, "read_json", read_json)

    with pytest.raises(SystemExit, match="must match Configuration metadata maximum=32"):
        governance.validate_configuration_schema()


def test_configuration_schema_v2_governance_rejects_default_outside_metadata_range(monkeypatch) -> None:
    def read_yaml(path: str):
        document = deepcopy(real_read_yaml(path))
        if path == "configs/model_serving.yaml":
            document["streaming"]["max_chunks"] = 1_000_001
        return document

    monkeypatch.setattr(governance, "read_yaml", read_yaml)

    with pytest.raises(SystemExit, match="streaming.max_chunks repository default exceeds metadata maximum"):
        governance.validate_configuration_schema()
