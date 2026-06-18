#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
_DIGEST_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


class MutableTagError(RuntimeError):
    """Raised when the running container's image is not pinned by sha256 digest."""


if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.main_model_control import load_main_model_catalog  # noqa: E402


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def capture_runtime_override(
    *,
    catalog_path: Path,
    compose_project: str,
) -> dict[str, Any] | None:
    catalog = load_main_model_catalog(catalog_path)
    service = str(catalog.runtime["compose_service"])
    command = [
        "docker",
        "ps",
        "-aq",
        "--filter",
        f"label=com.docker.compose.service={service}",
    ]
    if compose_project:
        command += [
            "--filter",
            f"label=com.docker.compose.project={compose_project}",
        ]
    ids = [
        line
        for line in subprocess.check_output(command, text=True).splitlines()
        if line
    ]
    if not ids:
        return None
    if len(ids) != 1:
        raise RuntimeError(
            f"expected one current container for {service}, found {len(ids)}"
        )
    inspected = json.loads(
        subprocess.check_output(["docker", "inspect", ids[0]], text=True)
    )[0]
    image = inspected.get("Config", {}).get("Image")
    runtime_command = inspected.get("Config", {}).get("Cmd")
    if not isinstance(image, str) or not image:
        raise RuntimeError("current main-model container image is missing")
    if not _DIGEST_IMAGE.fullmatch(image):
        # Container started with a mutable tag — resolve via RepoDigests for a pullable pinned ref.
        image_id = inspected.get("Image", "")
        repo_digests = json.loads(
            subprocess.check_output(
                ["docker", "image", "inspect", image_id, "--format", "{{json .RepoDigests}}"],
                text=True,
            )
        )
        pinned = next(
            (ref for ref in (repo_digests or []) if _DIGEST_IMAGE.fullmatch(ref)),
            None,
        )
        if pinned is None:
            raise MutableTagError(
                f"current main-model container image is not pinned by sha256 digest "
                f"and has no RepoDigests: {image!r}"
            )
        image = pinned
    if not isinstance(runtime_command, list) or not all(
        isinstance(value, str) for value in runtime_command
    ):
        raise RuntimeError("current main-model container command is invalid")
    return {
        "services": {
            service: {
                "image": image,
                "command": runtime_command,
            }
        }
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture the current main-model image and command for deployment rollback."
    )
    parser.add_argument(
        "--catalog", type=Path, default=ROOT / "configs/main_model_profiles.yaml"
    )
    parser.add_argument("--compose-project", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = capture_runtime_override(
            catalog_path=args.catalog,
            compose_project=args.compose_project,
        )
    except MutableTagError as exc:
        print(f"[capture] WARNING: {exc}; rollback capture skipped", file=sys.stderr)
        print("mutable-tag")
        return 0
    if document is None:
        print("missing")
        return 0
    _atomic_write(args.output, document)
    print("captured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
