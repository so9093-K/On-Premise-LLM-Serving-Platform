"""prepare_model_snapshot이 정확한 revision을 먼저 내려받고 로컬 캐시로
재검증하는 2단계 흐름(local_files_only=False 다음 True)을 따르는지 검증한다."""

from __future__ import annotations

from pathlib import Path

import huggingface_hub

from ai_model_serving.model_cache import prepare_model_snapshot


def test_prepare_downloads_exact_revision_then_verifies_local_cache(
    tmp_path, monkeypatch
):
    snapshot = tmp_path / "models--org--model" / "snapshots" / ("a" * 40)
    snapshot.mkdir(parents=True)
    calls: list[dict] = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        return str(snapshot)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    result = prepare_model_snapshot(
        model_id="org/model",
        revision="a" * 40,
        cache_dir=tmp_path,
        token="secret",
    )

    assert result.snapshot_path == snapshot
    assert [call.get("local_files_only", False) for call in calls] == [False, True]
    assert all(call["revision"] == "a" * 40 for call in calls)
    assert all(call["repo_id"] == "org/model" for call in calls)
