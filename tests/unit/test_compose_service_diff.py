from __future__ import annotations

import json

from scripts.compose import compose_service_diff


def test_service_diff_ignores_release_paths_and_reports_effective_changes(tmp_path, capsys):
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before_release = "/opt/platform/releases/old"
    after_release = "/opt/platform/releases/new"
    before.write_text(
        json.dumps(
            {
                "services": {
                    "gateway": {
                        "environment": {"MODE": "old"},
                        "volumes": [{"source": f"{before_release}/configs", "target": "/app/configs"}],
                    },
                    "main-llm-vllm": {
                        "image": "vllm@sha256:stable",
                        "volumes": [{"source": f"{before_release}/models", "target": "/models"}],
                    },
                    "removed-service": {"image": "old"},
                }
            }
        ),
        encoding="utf-8",
    )
    after.write_text(
        json.dumps(
            {
                "services": {
                    "gateway": {
                        "environment": {"MODE": "new"},
                        "volumes": [{"source": f"{after_release}/configs", "target": "/app/configs"}],
                    },
                    "main-llm-vllm": {
                        "image": "vllm@sha256:stable",
                        "volumes": [{"source": f"{after_release}/models", "target": "/models"}],
                    },
                    "new-service": {"image": "new"},
                }
            }
        ),
        encoding="utf-8",
    )

    result = compose_service_diff.main(
        [
            "--before", str(before),
            "--after", str(after),
            "--strip-before", before_release,
            "--strip-after", after_release,
        ]
    )

    assert result == 0
    assert capsys.readouterr().out.splitlines() == ["gateway", "new-service"]
