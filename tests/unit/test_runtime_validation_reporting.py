from __future__ import annotations

import json

from scripts.validation.runtime.reporting import write_reports


def test_runtime_report_preserves_qualification_identity_context(tmp_path) -> None:
    context = {
        "started": {"profile_id": "gemma4-e4b-it", "image_digest": "sha256:" + "a" * 64},
        "finished": {"profile_id": "gemma4-e4b-it", "image_digest": "sha256:" + "a" * 64},
        "stable": True,
        "errors": [],
    }

    json_path, _ = write_reports(
        root=tmp_path,
        output_dir="reports/runtime",
        version="0.0.1",
        session_started="2026-09-19T01:00:00+00:00",
        mode="live",
        results=[],
        qualification_context=context,
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["qualification_context"] == context
