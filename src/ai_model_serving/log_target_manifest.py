"""Compose 컨테이너 로그를 Alloy target으로 투영한다.

Docker API 권한은 admin-sidecar에만 둔다. Alloy는 이 모듈이 만든 읽기 전용
manifest와 json-file 로그만 읽으므로, 로그 수집기가 Docker 제어 권한을 갖지 않는다.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping


_DOCKER_LOG_ROOT = "/var/lib/docker/containers/"


def build_targets(containers: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Docker inspect 결과에서 실행 중 Compose 서비스의 Alloy file targets를 만든다.

    Docker가 반환한 ``LogPath``를 그대로 사용한다. container ID로 경로를 조립하지
    않아 Docker data-root 변경이나 logging driver 차이를 조용히 잘못 처리하지 않는다.
    """
    targets: list[dict[str, Any]] = []
    for container in containers:
        container_id = str(container.get("Id") or "")
        log_path = str(container.get("LogPath") or "")
        labels = container.get("Config", {}).get("Labels", {})
        if not isinstance(labels, Mapping):
            continue
        service = str(labels.get("com.docker.compose.service") or "")
        if not container_id or not service or not log_path.startswith(_DOCKER_LOG_ROOT):
            continue
        targets.append(
            {
                "targets": ["localhost"],
                "labels": {
                    "__path__": log_path,
                    "container_id": container_id,
                    "job": "docker",
                    "service": service,
                },
            }
        )
    return sorted(targets, key=lambda target: (target["labels"]["service"], target["labels"]["container_id"]))


def write_manifest(path: Path, targets: Iterable[Mapping[str, Any]]) -> None:
    """Alloy가 부분 파일을 읽지 않도록 manifest를 원자적으로 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # discovery.file은 Prometheus file-SD 호환의 *배열* 형식만 받는다.
    # schema_version 같은 wrapper를 두면 Alloy가 target을 읽지 못한다.
    payload = list(targets)
    fd, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
