"""Pinned Hugging Face snapshot preparation for main-model profiles."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PreparedModelSnapshot:
    model_id: str
    revision: str
    snapshot_path: Path


def default_hf_hub_cache_dir() -> Path:
    """Return the cache root used by Hugging Face Hub and vLLM."""
    explicit = os.environ.get("HF_HUB_CACHE")
    if explicit:
        return Path(explicit)
    return Path(os.environ.get("HF_HOME", "/root/.cache/huggingface")) / "hub"


def prepare_model_snapshot(
    *,
    model_id: str,
    revision: str,
    cache_dir: Path,
    token: str | None = None,
) -> PreparedModelSnapshot:
    """Download and locally re-open one exact revision in the HF hub cache.

    ``cache_dir`` is the hub cache root (normally ``HF_HOME/hub``), not
    ``HF_HOME`` itself. Keeping that boundary explicit prevents the preparer and
    vLLM from creating parallel ``models--*`` trees.
    """
    from huggingface_hub import snapshot_download

    cache_dir.mkdir(parents=True, exist_ok=True)
    resolved_token = token or os.environ.get("HF_TOKEN") or os.environ.get(
        "HUGGING_FACE_HUB_TOKEN"
    )
    downloaded = Path(
        snapshot_download(
            repo_id=model_id,
            revision=revision,
            cache_dir=str(cache_dir),
            token=resolved_token or None,
        )
    )
    verified = Path(
        snapshot_download(
            repo_id=model_id,
            revision=revision,
            cache_dir=str(cache_dir),
            token=resolved_token or None,
            local_files_only=True,
        )
    )
    if downloaded.resolve() != verified.resolve():
        raise RuntimeError(
            "Hugging Face cache verification resolved a different snapshot path"
        )
    if not verified.is_dir():
        raise RuntimeError(f"prepared Hugging Face snapshot is missing: {verified}")
    return PreparedModelSnapshot(model_id, revision, verified)
