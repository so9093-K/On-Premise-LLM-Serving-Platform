from __future__ import annotations

import json

from ai_model_serving.log_target_manifest import build_targets, write_manifest


def test_manifest_uses_docker_log_path_and_compose_service_label(tmp_path):
    """권한 있는 sidecar와 권한 없는 Alloy 사이의 target 계약을 고정한다."""
    targets = build_targets(
        [
            {
                "Id": "a" * 64,
                "LogPath": "/var/lib/docker/containers/a/log.json",
                "Config": {"Labels": {"com.docker.compose.service": "gateway"}},
            },
            {
                "Id": "b" * 64,
                "LogPath": "/not-the-mounted-docker-root/log.json",
                "Config": {"Labels": {"com.docker.compose.service": "other"}},
            },
        ]
    )

    assert targets == [
        {
            "targets": ["localhost"],
            "labels": {
                "__path__": "/var/lib/docker/containers/a/log.json",
                "container_id": "a" * 64,
                "job": "docker",
                "service": "gateway",
            },
        }
    ]

    manifest = tmp_path / "docker-containers.json"
    write_manifest(manifest, targets)
    assert json.loads(manifest.read_text(encoding="utf-8")) == targets
