from __future__ import annotations

import json

import pytest

from ai_model_serving.services.runtime_state import RuntimeStateStore, RuntimeStateStoreError


def test_deploy_directive_fails_startup_when_persistence_is_unavailable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "runtime-state.json"

    def fail_write(self: RuntimeStateStore) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(RuntimeStateStore, "_write_file", fail_write)

    with pytest.raises(
        RuntimeStateStoreError,
        match="failed to persist runtime desired state deploy directive",
    ):
        RuntimeStateStore(
            path,
            controllable_keys={"prompt_injection_detector"},
            deferred_keys=("prompt_injection_detector",),
            release_id="release-1",
        )


def test_boolean_schema_version_is_quarantined_instead_of_treated_as_v1(tmp_path) -> None:
    path = tmp_path / "runtime-state.json"
    path.write_text(
        json.dumps({"schema_version": True, "states": {"embedding": "stopped"}}),
        encoding="utf-8",
    )

    store = RuntimeStateStore(path, controllable_keys={"embedding"})

    assert store.recovery_quarantine_path is not None
    recovered = json.loads(path.read_text(encoding="utf-8"))
    assert recovered["states"]["embedding"]["state"] == "stopped"
    assert recovered["states"]["embedding"]["source"] == "recovery"
